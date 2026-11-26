#!/usr/bin/env python3
"""
Cite — resolve DOIs and cross-check against a personal Zotero library.

Config: ~/.config/zotero/config.json  ({"user_id": int, "api_key": str})

Usage
-----
  cite.py --doi 10.1001/archneurol.2010.218
      Fetch Crossref metadata for the DOI; if the paper is in the user's
      Zotero library, report item key, tags, notes, and PDF-text availability.

  cite.py --doi <DOI> --full
      Also print Zotero notes and the text of any attached PDF or HTML
      snapshot. Tries the Zotero /fulltext index first; falls back to
      downloading the attachment file (handles snapshot ZIPs, raw HTML,
      and PDFs via pdftotext if installed).

  cite.py --doi <DOI> --full --text-chars 0
      Print the entire extracted attachment text (no truncation).

  cite.py --pmid 41290621
      Resolve a PubMed ID to its DOI via NCBI, then run the same pipeline as
      --doi (Crossref metadata + Zotero library check + optional full text).

  cite.py --pmid 41290621 --full
      Same as above, also print Zotero notes and attachment text.

  cite.py --search "Jun 2010 APOE CR1 CLU PICALM"
      Query Crossref; print the top candidate matches with DOIs so a cited
      reference can be picked.

  cite.py --doi <DOI> --bib
      Emit a BibTeX entry from Crossref metadata.
"""
import argparse, json, os, sys, urllib.parse, urllib.request, urllib.error

CONFIG_PATH = os.path.expanduser("~/.config/zotero/config.json")
CROSSREF_WORKS = "https://api.crossref.org/works"
ZOTERO_BASE = "https://api.zotero.org"
NCBI_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
UA = "cite.py/0.1 (mailto:eugene.duff@gmail.com)"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        return None, e


def crossref_by_doi(doi):
    url = f"{CROSSREF_WORKS}/{urllib.parse.quote(doi, safe='/')}"
    body, _ = http_get(url)
    if not body:
        return None
    return json.loads(body).get("message")


def crossref_search(query, rows=5):
    url = f"{CROSSREF_WORKS}?query={urllib.parse.quote(query)}&rows={rows}"
    body, _ = http_get(url)
    if not body:
        return []
    return json.loads(body).get("message", {}).get("items", [])


def format_authors(msg, n=3):
    authors = msg.get("author", []) or []
    names = []
    for a in authors[:n]:
        fam = a.get("family", "")
        giv = a.get("given", "")
        init = "".join(p[0] + "." for p in giv.split()) if giv else ""
        names.append(f"{fam} {init}".strip())
    if len(authors) > n:
        names.append("et al.")
    return ", ".join(names)


def crossref_year(msg):
    issued = msg.get("issued", {}).get("date-parts", [[None]])
    return issued[0][0] if issued and issued[0] else None


def format_crossref(msg):
    import re
    raw_title = (msg.get("title") or [""])[0]
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_title)).strip()
    journal = (msg.get("container-title") or [""])[0]
    year = crossref_year(msg)
    vol = msg.get("volume", "")
    issue = msg.get("issue", "")
    pages = msg.get("page", "")
    doi = msg.get("DOI", "")
    abstract = msg.get("abstract", "")
    itemtype = msg.get("type", "")
    cited_by = msg.get("is-referenced-by-count", 0)
    authors = format_authors(msg, n=5)
    loc = f"{journal}"
    if vol:
        loc += f" {vol}"
    if issue:
        loc += f"({issue})"
    if pages:
        loc += f":{pages}"
    lines = [
        f"Authors:  {authors}",
        f"Year:     {year}",
        f"Title:    {title}",
        f"Journal:  {loc}",
        f"Type:     {itemtype}",
        f"Cited-by: {cited_by}",
        f"DOI:      {doi}",
        f"URL:      https://doi.org/{doi}",
    ]
    if abstract:
        clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", abstract)).strip()
        lines += ["", "Abstract:", clean]
    return "\n".join(lines)


def crossref_to_bibtex(msg):
    year = crossref_year(msg) or "nd"
    authors = msg.get("author", []) or []
    first_fam = authors[0].get("family", "anon") if authors else "anon"
    title = (msg.get("title") or [""])[0]
    first_word = next((w for w in title.split() if w[0].isalpha() and w.lower() not in {"the","a","an","of","on","and","in"}), "paper")
    key = f"{first_fam.lower()}{year}{first_word.lower()}"
    author_str = " and ".join(f"{a.get('family','')}, {a.get('given','')}" for a in authors)
    journal = (msg.get("container-title") or [""])[0]
    fields = {
        "title": title,
        "author": author_str,
        "journal": journal,
        "year": str(year),
        "volume": msg.get("volume", ""),
        "number": msg.get("issue", ""),
        "pages": msg.get("page", ""),
        "doi": msg.get("DOI", ""),
    }
    entry = [f"@article{{{key},"]
    for k, v in fields.items():
        if v:
            entry.append(f"  {k} = {{{v}}},")
    entry.append("}")
    return "\n".join(entry)


def zotero_find_by_doi(cfg, doi, msg=None):
    """Search Zotero library for a DOI. Returns list of matching top-level items,
    ALL duplicates included, sorted so items with attachments come first.

    Zotero bug workaround: items saved via browser connector often lack a stored
    DOI field, so DOI-exact matching misses the real item with attachments.
    Strategies 1+2 do title-based searches and accept items even when their DOI
    field is empty, as long as the title closely matches the Crossref record.
    """
    if not cfg:
        return []
    headers = {"Zotero-API-Key": cfg["api_key"]}
    doi_norm = doi.lower().strip()
    seen_keys = set()
    hits = []

    import re as _re

    def _title_norm(t):
        return _re.sub(r"\s+", " ", _re.sub(r"[^a-z0-9 ]", " ",
                       _re.sub(r"<[^>]+>", " ", (t or "").lower()))).strip()

    def _collect(items, crossref_title_norm=None, require_doi_match=False):
        for it in items:
            if it["key"] in seen_keys:
                continue
            data = it.get("data", {})
            item_doi = (data.get("DOI") or "").lower().strip()
            extra = (data.get("extra") or "").lower()
            doi_match = (item_doi == doi_norm or doi_norm in extra)
            # Title match: accept if item has no DOI stored (Zotero connector bug)
            # and the Zotero title closely matches the Crossref title.
            title_match = False
            if crossref_title_norm and not item_doi:
                item_title_norm = _title_norm(data.get("title", ""))
                # Accept if first 6 words agree (enough to avoid false positives)
                cr_words = crossref_title_norm.split()[:6]
                it_words = item_title_norm.split()[:6]
                if cr_words and cr_words == it_words:
                    title_match = True
            if (doi_match or (title_match and not require_doi_match)):
                seen_keys.add(it["key"])
                hits.append(it)

    if msg is None:
        msg = crossref_by_doi(doi)

    crossref_title_norm = None
    if msg:
        raw_title = (msg.get("title") or [""])[0]
        crossref_title_norm = _title_norm(raw_title)

    # Strategy 1: title+year search — fast, works when DOI indexed correctly
    if msg:
        first_words = " ".join(_title_norm((msg.get("title") or [""])[0]).split()[:4])
        year = crossref_year(msg)
        q1 = urllib.parse.quote(f"{first_words} {year or ''}".strip())
        url1 = (f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/top"
                f"?q={q1}&qmode=titleCreatorYear&limit=25&format=json")
        body1, _ = http_get(url1, headers=headers, timeout=20)
        if body1:
            _collect(json.loads(body1), crossref_title_norm=crossref_title_norm)

    # Strategy 2: broader title-only search — catches items saved without DOI
    if msg:
        first_words8 = " ".join(_title_norm((msg.get("title") or [""])[0]).split()[:8])
        q2 = urllib.parse.quote(first_words8)
        url2 = (f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/top"
                f"?q={q2}&qmode=titleCreatorYear&limit=25&format=json")
        body2, _ = http_get(url2, headers=headers, timeout=20)
        if body2:
            _collect(json.loads(body2), crossref_title_norm=crossref_title_norm)

    if not hits:
        return []

    # Sort: items with children (attachments/notes) first
    def _n_children(it):
        children = zotero_children(cfg, it["key"])
        it["_children_cache"] = children          # cache so describe_zotero_match can reuse
        imported = sum(1 for c in children
                       if c["data"].get("linkMode") in ("imported_file", "imported_url"))
        return -imported                           # negative for descending sort

    hits.sort(key=_n_children)
    return hits


def zotero_children(cfg, item_key):
    """Fetch attachments + notes under a parent item."""
    headers = {"Zotero-API-Key": cfg["api_key"]}
    url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/{item_key}/children?format=json"
    body, _ = http_get(url, headers=headers)
    if not body:
        return []
    return json.loads(body)


def _html_to_text(html):
    import re
    t = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<style[^>]*>.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#x27;", "'"),
                    ("&#39;", "'"), ("&apos;", "'")):
        t = t.replace(ent, ch)
    return re.sub(r"\s+", " ", t).strip()


def zotero_attachment_text(cfg, attachment):
    """Try /fulltext first; fall back to /file (handles snapshot ZIPs, raw HTML, PDFs).

    Returns (text, source_label) or (None, reason)."""
    headers = {"Zotero-API-Key": cfg["api_key"]}
    key = attachment["key"]
    content_type = (attachment["data"].get("contentType") or "").lower()

    url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/{key}/fulltext"
    body, _ = http_get(url, headers=headers)
    if body:
        try:
            content = json.loads(body).get("content", "")
            if content:
                return content, "fulltext-index"
        except Exception:
            pass

    url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/{key}/file"
    body, err = http_get(url, headers=headers)
    if not body:
        # Try WebDAV (Koofr or other) if configured — Zotero stores files as {key}.zip
        webdav_url = cfg.get("webdav_url", "").rstrip("/")
        if webdav_url:
            import base64
            wuser = cfg.get("webdav_user", "")
            wpass = cfg.get("webdav_password", "")
            token = base64.b64encode(f"{wuser}:{wpass}".encode()).decode()
            dav_file_url = f"{webdav_url}/{key}.zip"
            dav_body, _ = http_get(dav_file_url,
                                   headers={"Authorization": f"Basic {token}"},
                                   timeout=20)
            if dav_body:
                body = dav_body
        if not body:
            # Try the Zotero desktop local HTTP server (only works when Zotero is running).
            local_url = f"http://localhost:23119/zotero/items/{key}/attachment"
            try:
                local_body, _ = http_get(local_url, headers={}, timeout=3)
                if local_body:
                    body = local_body
            except Exception:
                pass
        if not body:
            link_mode = attachment["data"].get("linkMode", "")
            reason = ("file-local-only (stored in Zotero desktop, not synced to cloud — "
                      "open Zotero → right-click attachment → 'Manage Attachments' → sync, "
                      "or export the PDF and drop it in the workspace)"
                      if link_mode in ("imported_file", "imported_url")
                      else "file-not-available")
            return None, reason

    import io, zipfile
    if body[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(body))
            html_names = [n for n in zf.namelist() if n.lower().endswith((".html", ".htm"))]
            if html_names:
                html = zf.read(html_names[0]).decode("utf-8", errors="replace")
                return _html_to_text(html), f"snapshot-zip:{html_names[0]}"
        except zipfile.BadZipFile:
            pass

    if content_type == "text/html" or body[:6].lower().startswith((b"<!doc", b"<html")):
        return _html_to_text(body.decode("utf-8", errors="replace")), "html"

    if content_type == "application/pdf" or body[:4] == b"%PDF":
        import subprocess
        try:
            r = subprocess.run(["pdftotext", "-", "-"], input=body,
                               capture_output=True, timeout=30)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", errors="replace"), "pdftotext"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None, "pdf-no-extractor (install poppler-utils for pdftotext)"

    if content_type.startswith("text/"):
        return body.decode("utf-8", errors="replace"), "text"

    return None, f"unsupported content-type: {content_type or 'unknown'}"


def describe_zotero_match(cfg, item, full=False, text_chars=4000):
    data = item["data"]
    key = item["key"]
    out = [f"\nZotero: IN LIBRARY (item {key})"]
    tags = [t["tag"] for t in data.get("tags", [])]
    if tags:
        out.append(f"  Tags: {', '.join(tags)}")
    if data.get("dateAdded"):
        out.append(f"  Added: {data['dateAdded'][:10]}")
    # Use cached children from sort step if available, else fetch
    children = item.get("_children_cache") or zotero_children(cfg, key)
    notes = [c for c in children if c["data"].get("itemType") == "note"]
    attachments = [c for c in children if c["data"].get("itemType") == "attachment"]
    pdf_attachments = [a for a in attachments
                       if (a["data"].get("contentType") or "").lower() == "application/pdf"]
    # Snapshots: content-type text/html OR imported_url link mode (ZIP snapshots)
    snapshot_attachments = [a for a in attachments
                            if (a["data"].get("contentType") or "").lower() == "text/html"
                            or a["data"].get("linkMode") in ("imported_url", "imported_file")]
    # De-dup with PDFs
    snapshot_attachments = [a for a in snapshot_attachments if a not in pdf_attachments]
    out.append(f"  Notes: {len(notes)}  Attachments: {len(attachments)} "
               f"(PDFs: {len(pdf_attachments)}, snapshots: {len(snapshot_attachments)})")
    if full and notes:
        out.append("\n  --- Notes ---")
        for n in notes:
            import re
            text = re.sub(r"<[^>]+>", "", n["data"].get("note", "")).strip()
            out.append("  " + text.replace("\n", "\n  "))
    if full and (pdf_attachments or snapshot_attachments):
        # Try all attachments in priority order: PDFs first, then snapshots.
        # Don't stop at the first — iterate until one yields text.
        all_targets = pdf_attachments + snapshot_attachments
        text = None
        last_source = "no-attachments"
        for target in all_targets:
            ctype = target["data"].get("contentType", "?")
            lmode = target["data"].get("linkMode", "")
            text, last_source = zotero_attachment_text(cfg, target)
            if text:
                shown = text if text_chars <= 0 else text[:text_chars]
                suffix = "" if text_chars <= 0 or len(text) <= text_chars else f" of {len(text)}"
                out.append(f"\n  --- Attachment text ({ctype}/{lmode} via {last_source}, "
                           f"first {len(shown)}{suffix} chars) ---")
                out.append("  " + shown.replace("\n", "\n  "))
                break
        if not text:
            out.append(f"  (could not extract attachment text from any attachment: {last_source})")
    return "\n".join(out)


def pmid_to_doi(pmid):
    """Resolve a PMID to a DOI via NCBI esummary. Returns DOI string or None."""
    params = urllib.parse.urlencode({"db": "pubmed", "id": str(pmid), "retmode": "json"})
    body, _ = http_get(f"{NCBI_ESUMMARY}?{params}")
    if not body:
        return None
    result = json.loads(body).get("result", {})
    summary = result.get(str(pmid), {})
    eloc = summary.get("elocationid", "")
    # elocationid is typically "doi: 10.xxxx/..." — strip prefix
    for part in eloc.split(";"):
        part = part.strip()
        if part.lower().startswith("doi:"):
            return part[4:].strip()
    return None


def cmd_pmid(args, cfg):
    pmid = args.pmid
    print(f"Resolving PMID:{pmid} via NCBI…", file=sys.stderr)
    doi = pmid_to_doi(pmid)
    if not doi:
        print(f"Could not resolve PMID:{pmid} to a DOI via NCBI esummary.", file=sys.stderr)
        return 1
    print(f"PMID:{pmid} → DOI:{doi}\n")
    # Reuse cmd_doi with the resolved DOI
    args.doi = doi
    return cmd_doi(args, cfg)


def cmd_doi(args, cfg):
    msg = crossref_by_doi(args.doi)
    if not msg:
        print(f"Crossref: no record for DOI {args.doi}", file=sys.stderr)
        return 1
    if args.bib:
        print(crossref_to_bibtex(msg))
        return 0
    print(format_crossref(msg))
    if cfg:
        hits = zotero_find_by_doi(cfg, args.doi, msg=msg)
        if hits:
            # Print best hit (sorted: most attachments first).
            # If --full and text extraction fails, fall back through all duplicate records.
            primary = describe_zotero_match(cfg, hits[0], full=args.full, text_chars=args.text_chars)
            print(primary)
            if len(hits) > 1:
                dupe_keys = ", ".join(h["key"] for h in hits[1:])
                print(f"  (+ {len(hits)-1} duplicate record(s) in library: {dupe_keys})")
                if args.full and "could not extract attachment text" in primary:
                    for dup in hits[1:]:
                        dup_desc = describe_zotero_match(cfg, dup, full=True,
                                                         text_chars=args.text_chars)
                        if "could not extract attachment text" not in dup_desc:
                            print(f"\n  [Full text found in duplicate record {dup['key']}]")
                            print(dup_desc)
                            break
        else:
            print("\nZotero: not in library")
    return 0


def cmd_merge_orphans(args, cfg):
    """Find standalone snapshot/webpage items with no parent and attempt to match
    them to existing journal-article records by URL or title, then re-parent them."""
    if not cfg:
        print("No Zotero config found.", file=sys.stderr)
        return 1
    headers = {"Zotero-API-Key": cfg["api_key"]}

    # Fetch all attachment items with no parent
    url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items?itemType=attachment&limit=100&format=json"
    body, _ = http_get(url, headers=headers)
    if not body:
        print("Could not fetch attachments.", file=sys.stderr)
        return 1
    all_attachments = json.loads(body)
    orphans = [a for a in all_attachments if not a["data"].get("parentItem")]

    if not orphans:
        print("No orphaned attachments found — library is clean.")
        return 0

    print(f"Found {len(orphans)} orphaned attachment(s):\n")
    import re

    for att in orphans:
        ad = att["data"]
        att_key = att["key"]
        att_title = ad.get("title", "")
        att_url = ad.get("url", "")
        print(f"  [{att_key}] '{att_title[:70]}'\n           url: {att_url[:80]}")

        # Try to find a matching top-level item by title
        first_words = " ".join(re.sub(r"[^\w\s]", " ", att_title).split()[:5])
        if not first_words:
            print("           → skipped (no title to match on)\n")
            continue

        q = urllib.parse.quote(first_words)
        search_url = (f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/top"
                      f"?q={q}&qmode=titleCreatorYear&limit=10&format=json")
        sbody, _ = http_get(search_url, headers=headers)
        candidates = json.loads(sbody) if sbody else []
        # Keep only journal articles / books (not attachments themselves)
        candidates = [c for c in candidates
                      if c["data"].get("itemType") not in ("attachment", "note")]

        if not candidates:
            print("           → no matching parent found\n")
            continue

        best = candidates[0]
        bd = best["data"]
        print(f"           → best match: [{best['key']}] {bd.get('title','')[:70]}")

        if args.dry_run:
            print("           → (dry-run: would re-parent)\n")
            continue

        # PATCH the attachment to set parentItem
        patch = json.dumps({"parentItem": best["key"]}).encode()
        patch_url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/{att_key}"
        req = urllib.request.Request(
            patch_url, data=patch, method="PATCH",
            headers={**headers, "Content-Type": "application/json",
                     "Zotero-API-Version": "3",
                     "If-Unmodified-Since-Version": str(att.get("version", 0))})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                print(f"           → re-parented ✓ (HTTP {r.status})\n")
        except urllib.error.HTTPError as e:
            print(f"           → PATCH failed: {e}\n")

    return 0


def cmd_search(args, cfg):
    items = crossref_search(args.search, rows=args.rows)
    if not items:
        print("No Crossref matches.", file=sys.stderr)
        return 1
    import re
    for i, msg in enumerate(items, 1):
        raw_title = (msg.get("title") or [""])[0]
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_title)).strip()
        year = crossref_year(msg)
        journal = (msg.get("container-title") or [""])[0]
        authors = format_authors(msg, n=3)
        doi = msg.get("DOI", "")
        vol = msg.get("volume", "")
        issue = msg.get("issue", "")
        pages = msg.get("page", "")
        itemtype = msg.get("type", "")
        cited_by = msg.get("is-referenced-by-count", 0)
        loc = journal
        if vol:
            loc += f" {vol}"
        if issue:
            loc += f"({issue})"
        if pages:
            loc += f":{pages}"
        flags = []
        if itemtype and itemtype != "journal-article":
            flags.append(itemtype)
        if cited_by:
            flags.append(f"cited-by={cited_by}")
        flag_str = f"  [{'; '.join(flags)}]" if flags else ""
        print(f"[{i}] {authors} ({year}) {title}")
        print(f"    {loc}  doi:{doi}{flag_str}")
        abstract = msg.get("abstract", "")
        if abstract:
            clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", abstract)).strip()
            snippet = clean[:args.abstract_chars].rstrip()
            if len(clean) > args.abstract_chars:
                snippet += "…"
            print(f"    {snippet}")
        print()
    return 0


def main():
    p = argparse.ArgumentParser(description="Resolve DOIs + cross-check Zotero library")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--doi", help="DOI to look up")
    g.add_argument("--pmid", help="PubMed ID to resolve to DOI, then look up")
    g.add_argument("--search", help="Free-text search (Crossref)")
    g.add_argument("--merge-orphans", action="store_true",
                   help="Find standalone snapshot items with no parent and re-attach them")
    p.add_argument("--full", action="store_true", help="Include Zotero notes + PDF text")
    p.add_argument("--bib", action="store_true", help="Emit BibTeX for --doi")
    p.add_argument("--rows", type=int, default=5, help="Search result count")
    p.add_argument("--abstract-chars", type=int, default=350,
                   help="Abstract snippet length in --search output")
    p.add_argument("--text-chars", type=int, default=4000,
                   help="Attachment text snippet length when --full is set (0 = no limit)")
    p.add_argument("--dry-run", action="store_true",
                   help="With --merge-orphans: show what would be done without patching")
    args = p.parse_args()
    cfg = load_config()
    if args.doi:
        return cmd_doi(args, cfg)
    if args.pmid:
        return cmd_pmid(args, cfg)
    if args.merge_orphans:
        return cmd_merge_orphans(args, cfg)
    return cmd_search(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
