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
    """Search Zotero library for a DOI. Returns list of matching top-level items."""
    if not cfg:
        return []
    headers = {"Zotero-API-Key": cfg["api_key"]}
    # Zotero's q= doesn't index the DOI field reliably. Best strategy:
    # search by title+year from Crossref metadata, then verify DOI field match.
    if msg is None:
        msg = crossref_by_doi(doi)
    if not msg:
        return []
    import re
    raw_title = (msg.get("title") or [""])[0]
    clean_title = re.sub(r"<[^>]+>", " ", raw_title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    first_words = " ".join(clean_title.split()[:4])
    year = crossref_year(msg)
    q = urllib.parse.quote(f"{first_words} {year or ''}".strip())
    url = f"{ZOTERO_BASE}/users/{cfg['user_id']}/items/top?q={q}&qmode=titleCreatorYear&limit=25&format=json"
    body, _ = http_get(url, headers=headers)
    if not body:
        return []
    items = json.loads(body)
    doi_norm = doi.lower().strip()
    hits = []
    for it in items:
        data = it.get("data", {})
        item_doi = (data.get("DOI") or "").lower().strip()
        extra = (data.get("extra") or "").lower()
        if item_doi == doi_norm or doi_norm in extra:
            hits.append(it)
    # Fallback: if nothing matched by DOI field but a single title+year result
    # exists, return it as a probable match.
    if not hits and len(items) == 1:
        hits = items
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
    body, _ = http_get(url, headers=headers)
    if not body:
        return None, "file-not-available"

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
    children = zotero_children(cfg, key)
    notes = [c for c in children if c["data"].get("itemType") == "note"]
    attachments = [c for c in children if c["data"].get("itemType") == "attachment"]
    pdf_attachments = [a for a in attachments if (a["data"].get("contentType") or "").lower() == "application/pdf"]
    html_attachments = [a for a in attachments if (a["data"].get("contentType") or "").lower() == "text/html"]
    out.append(f"  Notes: {len(notes)}  Attachments: {len(attachments)} "
               f"(PDFs: {len(pdf_attachments)}, snapshots: {len(html_attachments)})")
    if full and notes:
        out.append("\n  --- Notes ---")
        for n in notes:
            import re
            text = re.sub(r"<[^>]+>", "", n["data"].get("note", "")).strip()
            out.append("  " + text.replace("\n", "\n  "))
    if full and (pdf_attachments or html_attachments):
        target = pdf_attachments[0] if pdf_attachments else html_attachments[0]
        ctype = target["data"].get("contentType", "?")
        text, source = zotero_attachment_text(cfg, target)
        if text:
            shown = text if text_chars <= 0 else text[:text_chars]
            suffix = "" if text_chars <= 0 or len(text) <= text_chars else f" of {len(text)}"
            out.append(f"\n  --- Attachment text ({ctype} via {source}, "
                       f"first {len(shown)}{suffix} chars) ---")
            out.append("  " + shown.replace("\n", "\n  "))
        else:
            out.append(f"  (could not extract attachment text: {source})")
    return "\n".join(out)


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
            for h in hits:
                print(describe_zotero_match(cfg, h, full=args.full, text_chars=args.text_chars))
        else:
            print("\nZotero: not in library")
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
    g.add_argument("--search", help="Free-text search (Crossref)")
    p.add_argument("--full", action="store_true", help="Include Zotero notes + PDF text")
    p.add_argument("--bib", action="store_true", help="Emit BibTeX for --doi")
    p.add_argument("--rows", type=int, default=5, help="Search result count")
    p.add_argument("--abstract-chars", type=int, default=350,
                   help="Abstract snippet length in --search output")
    p.add_argument("--text-chars", type=int, default=4000,
                   help="Attachment text snippet length when --full is set (0 = no limit)")
    args = p.parse_args()
    cfg = load_config()
    if args.doi:
        return cmd_doi(args, cfg)
    return cmd_search(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
