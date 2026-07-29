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
