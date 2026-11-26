"""
figutils — project-agnostic figure saving with .provenance tracking.

Drop this file into any project's scripts/ or utils/ directory (or symlink it),
then use:

    from figutils import save_fig
    save_fig(fig, '/path/to/figure.png', script=__file__)

A companion .provenance file is written alongside every figure so the generating
script can always be traced, even months later.

Provenance file location:
    <figure_dir>/.provenance/<figure_filename>.provenance

Provenance file format (plain text, key: value):
    figure:  bar.png
    script:  /abs/path/to/generating_script.py
    saved:   2026-07-24T09:45:12
    commit:  a3f8c21          # short git SHA of HEAD, or 'unknown'
    data_dir: /path/to/project  # optional, set if caller passes data_dir
"""
from pathlib import Path
import datetime
import subprocess
import inspect


def save_fig(fig, path, script=None, dpi=160, data_dir=None, **kwargs):
    """Save a matplotlib Figure and write a .provenance companion file.

    Parameters
    ----------
    fig      : matplotlib.figure.Figure
    path     : str or Path — absolute path to the output file.
               If relative and data_dir is provided, resolved as data_dir / path.
               If relative and data_dir is None, resolved relative to cwd.
    script   : str or Path or None — path of the generating script.
               Auto-detected from the call stack if None.
    dpi      : int — passed to savefig (default 160).
    data_dir : str or Path or None — project root used to resolve relative paths
               and for git rev-parse. Auto-detected (nearest .git ancestor of path)
               if None.
    **kwargs — forwarded to fig.savefig (e.g. bbox_inches='tight').

    Returns
    -------
    Path — absolute path where the figure was saved.
    """
    path = Path(path)
    if not path.is_absolute():
        if data_dir is not None:
            path = Path(data_dir) / path
        else:
            path = Path.cwd() / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    kwargs.setdefault('bbox_inches', 'tight')
    kwargs.setdefault('facecolor', 'white')
    fig.savefig(str(path), dpi=dpi, **kwargs)

    # Auto-detect calling script from stack if not supplied
    if script is None:
        for frame in inspect.stack()[1:]:
            if frame.filename and frame.filename != __file__ and \
               not frame.filename.startswith('<'):
                script = frame.filename
                break

    # Resolve git commit — try from figure dir, then data_dir, then script dir
    commit = 'unknown'
    search_dirs = [path.parent]
    if data_dir:
        search_dirs.append(Path(data_dir))
    if script:
        search_dirs.append(Path(script).parent)
    for d in search_dirs:
        try:
            commit = subprocess.check_output(
                ['git', '-C', str(d), 'rev-parse', '--short', 'HEAD'],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            break
        except Exception:
            continue

    # Write provenance
    prov_dir = path.parent / '.provenance'
    prov_dir.mkdir(exist_ok=True)
    prov_path = prov_dir / (path.name + '.provenance')

    lines = [
        f"figure:   {path.name}",
        f"script:   {script or '<unknown>'}",
        f"saved:    {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"commit:   {commit}",
    ]
    if data_dir:
        lines.append(f"data_dir: {data_dir}")
    prov_path.write_text('\n'.join(lines) + '\n')

    print(f'Saved: {path}')
    return path


def read_provenance(fig_path):
    """Read the provenance record for a saved figure.

    Parameters
    ----------
    fig_path : str or Path — path to the figure file (not the .provenance file).

    Returns
    -------
    dict with keys: figure, script, saved, commit, data_dir (optional).
    Returns {} if no provenance file exists.
    """
    fig_path = Path(fig_path).resolve()
    prov_path = fig_path.parent / '.provenance' / (fig_path.name + '.provenance')
    if not prov_path.exists():
        return {}
    result = {}
    for line in prov_path.read_text().splitlines():
        if ':' in line:
            key, _, val = line.partition(':')
            result[key.strip()] = val.strip()
    return result


def provenance_path(fig_path):
    """Return the expected .provenance file path for a figure (may not exist yet)."""
    fig_path = Path(fig_path).resolve()
    return fig_path.parent / '.provenance' / (fig_path.name + '.provenance')


def archive_fig(fig_path, reason=None):
    """Move a figure and its provenance file into <fig_dir>/_archived/.

    The figure and its .provenance companion are moved atomically (rename) so
    nothing is left half-done on error.  An ``archived:`` timestamp and optional
    ``reason:`` line are appended to the provenance before the move.

    Parameters
    ----------
    fig_path : str or Path — path to the figure file to archive.
    reason   : str or None — optional note explaining why it was archived,
               written into the provenance record.

    Returns
    -------
    Path — new location of the archived figure.

    Raises
    ------
    FileNotFoundError if the figure file does not exist.
    """
    import shutil
    fig_path = Path(fig_path).resolve()
    if not fig_path.exists():
        raise FileNotFoundError(f"Figure not found: {fig_path}")

    archive_dir = fig_path.parent / '_archived'
    archive_dir.mkdir(exist_ok=True)
    archive_prov_dir = archive_dir / '.provenance'
    archive_prov_dir.mkdir(exist_ok=True)

    src_prov = fig_path.parent / '.provenance' / (fig_path.name + '.provenance')
    dst_fig  = archive_dir / fig_path.name
    dst_prov = archive_prov_dir / (fig_path.name + '.provenance')

    # Append archive metadata to provenance before moving
    if src_prov.exists():
        lines = src_prov.read_text().rstrip('\n').splitlines()
    else:
        lines = [f"figure:   {fig_path.name}", "script:   <unknown>"]
    lines.append(f"archived: {datetime.datetime.now().isoformat(timespec='seconds')}")
    if reason:
        lines.append(f"reason:   {reason}")
    updated_prov = '\n'.join(lines) + '\n'

    # Write updated provenance to destination first, then move the figure
    dst_prov.write_text(updated_prov)
    shutil.move(str(fig_path), str(dst_fig))
    # Remove old provenance only after figure is safely moved
    if src_prov.exists():
        src_prov.unlink()

    print(f"Archived: {fig_path.name} → {dst_fig}")
    return dst_fig
