# figutils — figure provenance skill

Every figure you generate must be saved via `save_fig` from this library so
that the generating script is always traceable. Follow these rules without
exception when writing figure-generating Python code.

---

## Setup (per project, once)

Copy or symlink `figutils.py` into the project's scripts directory:

```bash
# symlink (preferred — gets updates automatically)
ln -s ~/code/claude_skills/figutils/figutils.py /path/to/project/scripts/figutils.py

# or copy
cp ~/code/claude_skills/figutils/figutils.py /path/to/project/scripts/figutils.py
```

Then ensure the scripts directory is on `sys.path` in each script:

```python
sys.path.insert(0, '/path/to/project/scripts')
from figutils import save_fig
```

---

## Saving figures — mandatory pattern

Always pass `script=__file__` so the provenance file records the exact source
file, not a stack-guessed path:

```python
from figutils import save_fig

out = Path('/abs/path/to/figure.png')
save_fig(fig, out, script=__file__)
plt.close()
print(f'Saved: {out}')
```

If the project has a canonical `DATA_DIR`, pass it so relative paths resolve
correctly and git lookup finds the right repo:

```python
save_fig(fig, 'project/figures/foo/bar.png', script=__file__, data_dir=DATA_DIR)
```

Never use `fig.savefig()` directly — it bypasses provenance tracking.

---

## Finding the generating script for a figure

**Always check `.provenance` first** before searching the codebase.
The provenance file is at:

```
<figure_dir>/.provenance/<figure_filename>.provenance
```

For example, for `project/figures/pathology/foo.png`:

```bash
cat project/figures/pathology/.provenance/foo.png.provenance
```

Output:
```
figure:   foo.png
script:   /abs/path/to/scripts/foo_analysis.py
saved:    2026-07-24T09:45:12
commit:   a3f8c21
```

In Python:
```python
from figutils import read_provenance
info = read_provenance('project/figures/pathology/foo.png')
print(info['script'])   # → /abs/path/to/scripts/foo_analysis.py
print(info['commit'])   # → a3f8c21
```

Only fall back to grep/find if the provenance file is missing or records
`script: <stdin>` (which happens if code was piped via heredoc — avoid that).

---

## Rules

1. **Never pipe script code via `python3 - << 'EOF'`** (heredoc stdin).
   Provenance records `script: <stdin>`, making figures non-reproducible.
   Always `Write` to a named `.py` file first, then run it.

2. **Never read image files to recover values.** Provenance → script → data.
   Recompute exact values from the source data and script.

3. **Always end figure-generating messages** by reporting the saved file path(s)
   so the user can open them immediately.

4. **One script per figure family.** Keep figures that share data loading in
   one script rather than splitting across many files.

---

## Provenance file format

Plain text, `key:   value` per line:

| Key       | Value                                              |
|-----------|----------------------------------------------------|
| `figure`  | Filename only (e.g. `foo.png`)                     |
| `script`  | Absolute path to the generating script             |
| `saved`   | ISO-8601 timestamp (seconds precision)             |
| `commit`  | Short git SHA of HEAD at save time, or `unknown`   |
| `data_dir`| Project root (optional, only if passed explicitly) |
