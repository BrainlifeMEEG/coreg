"""
app-coreg-v2: Automated MEG/EEG coregistration to MRI.

Inputs : raw or epochs FIF (sensor positions + digitization),
         FreeSurfer subject directory.
Outputs: trans.fif (MRI-to-head transform), quality report.
"""

import os
import sys
import numpy as np

# Headless 3D rendering — must be set BEFORE vtk/pyvista/mne.viz is imported.
# QT_QPA_PLATFORM=offscreen lets Qt init without X11 (bypasses MNE's _display_is_valid check).
# VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN=1 tells VTK to render offscreen (uses OSMesa if available).
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN', '1')
os.environ.setdefault('MPLBACKEND', 'Agg')

# Set up FreeSurfer environment (needed for make_scalp_surfaces / mkheadsurf).
# mkheadsurf requires the full FreeSurfer env (MNI_DIR, PERL5LIB, etc.),
# not just FREESURFER_HOME + PATH.
import subprocess as _sp
if not os.environ.get('FREESURFER_HOME'):
    for _candidate in ['/usr/local/freesurfer', '/opt/freesurfer', '/usr/share/freesurfer']:
        if os.path.isdir(os.path.join(_candidate, 'bin')):
            os.environ['FREESURFER_HOME'] = _candidate
            break
fs_home = os.environ.get('FREESURFER_HOME', '')
if fs_home:
    _setup = os.path.join(fs_home, 'SetUpFreeSurfer.sh')
    if os.path.isfile(_setup):
        # Source SetUpFreeSurfer.sh and capture the resulting environment.
        # Use 'env -i' to start from a clean env so we can see what SetUpFreeSurfer
        # actually sets, then merge back. We keep the original env and ADD what's new.
        _result = _sp.run(
            ['bash', '-c', f'source {_setup} 2>/dev/null && env'],
            capture_output=True, text=True,
            env=dict(os.environ)  # pass current env so bash can find 'source'
        )
        for _line in _result.stdout.splitlines():
            _k, _, _v = _line.partition('=')
            if _k and not _k.startswith('_') and _k.isidentifier():
                # Always update PATH (need to append FS bin dirs, not just setdefault)
                if _k == 'PATH':
                    # Merge: keep existing PATH entries, add new FS entries
                    _existing = set(os.environ.get('PATH', '').split(':'))
                    for _p in _v.split(':'):
                        if _p and _p not in _existing:
                            os.environ['PATH'] = _p + ':' + os.environ['PATH']
                else:
                    os.environ.setdefault(_k, _v)
    # Explicitly set critical FreeSurfer vars as fallback if sourcing didn't provide them
    for _k, _rel in [
        ('MNI_DIR',      'mni'),
        ('MINC_BIN_DIR', os.path.join('mni', 'bin')),
        ('MINC_LIB_DIR', os.path.join('mni', 'lib')),
        ('FSF_OUTPUT_FORMAT', 'nii.gz'),
    ]:
        os.environ.setdefault(_k, os.path.join(fs_home, _rel))
    # Ensure FreeSurfer bin dirs are in PATH
    for _bin in [os.path.join(fs_home, 'bin'), os.path.join(fs_home, 'mni', 'bin')]:
        if os.path.isdir(_bin) and _bin not in os.environ.get('PATH', ''):
            os.environ['PATH'] = _bin + ':' + os.environ['PATH']
    # Perl libs needed by mkheadsurf (a Perl script)
    _perl5 = os.path.join(fs_home, 'mni', 'lib', 'perl5', '5.8.5')
    if os.path.isdir(_perl5):
        _cur = os.environ.get('PERL5LIB', '')
        os.environ['PERL5LIB'] = (_perl5 + ':' + _cur) if _cur else _perl5

# Resolve brainlife_utils — try local copy first, then parent monorepo
app_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(app_dir)
for search_path in [app_dir, parent_dir]:
    if os.path.isdir(os.path.join(search_path, 'brainlife_utils')):
        sys.path.insert(0, search_path)
        break

from brainlife_utils import (
    setup_matplotlib_backend,
    load_config,
    ensure_output_dirs,
    add_info_to_product,
    add_image_to_product,
    create_product_json,
    setup_offscreen_3d_backend,
)

setup_matplotlib_backend()
import matplotlib.pyplot as plt

import mne
from mne.io.constants import FIFF

# == SETUP ==
ensure_output_dirs('out_dir', 'out_figs', 'out_dir_report')
report_items = []
report = mne.Report(title='Coregistration Report')  # created early so alignment steps below can add views

# == LOAD CONFIG ==
config = load_config()
config = {k.strip(): v for k, v in config.items()}  # strip tabs/spaces from keys (Brainlife UI bug)

# == LOAD SENSOR DATA (for info + digitization) ==
epochs_file = config.get('epochs') or config.get('epo') or None
raw_file = config.get('raw') or config.get('mne') or None

try:
    if epochs_file and os.path.isfile(epochs_file):
        data = mne.read_epochs(epochs_file, preload=False)
    elif raw_file and os.path.isfile(raw_file):
        data = mne.io.read_raw_fif(raw_file, preload=False)
    else:
        add_info_to_product(
            report_items,
            "FATAL: No sensor data found. Set 'epochs' or 'raw' in config.json.",
            "error"
        )
        create_product_json(report_items)
        sys.exit(1)
except Exception as e:
    add_info_to_product(report_items, f"FATAL: Could not load sensor data: {e}", "error")
    create_product_json(report_items)
    sys.exit(1)

info = data.info

# Detect modality
ch_types  = info.get_channel_types()
meg_types = {'mag', 'grad', 'ref_meg'}
eeg_count = sum(1 for t in ch_types if t == 'eeg')
meg_count = sum(1 for t in ch_types if t in meg_types)

if meg_count > 0 and eeg_count > 0:
    modality = 'meeg'
elif meg_count > 0:
    modality = 'meg'
elif eeg_count > 0:
    modality = 'eeg'
else:
    add_info_to_product(
        report_items,
        f"FATAL: No MEG or EEG channels found. Types: {set(ch_types)}",
        "error"
    )
    create_product_json(report_items)
    sys.exit(1)

add_info_to_product(
    report_items,
    f"Modality: {modality} | EEG: {eeg_count} | MEG: {meg_count}",
    "info"
)

# == RESOLVE FREESURFER DIRECTORY ==
# Brainlife passes the FreeSurfer subject directory as 'freesurfer'.
# We derive subjects_dir (parent) and subject (basename) from it.
fs_path      = config.get('freesurfer') or config.get('output')
subjects_dir = config.get('subjects_dir')
subject      = config.get('subject')

if fs_path and os.path.isdir(fs_path):
    fs_path = os.path.abspath(fs_path)
    if not subjects_dir or not subject:
        if os.path.isdir(os.path.join(fs_path, 'mri')):
            # fs_path IS the FreeSurfer subject directory
            subjects_dir = subjects_dir or os.path.dirname(fs_path)
            subject = subject or os.path.basename(fs_path)
        else:
            # fs_path is a container (neuro/freesurfer datatype dir) — find subject inside
            _subdirs = sorted([
                d for d in os.listdir(fs_path)
                if os.path.isdir(os.path.join(fs_path, d, 'mri'))
            ])
            if _subdirs:
                subjects_dir = subjects_dir or fs_path
                subject = subject or _subdirs[0]
            else:
                # fallback: treat basename as subject
                subjects_dir = subjects_dir or os.path.dirname(fs_path)
                subject = subject or os.path.basename(fs_path)

if not subjects_dir or not subject:
    add_info_to_product(
        report_items,
        "FATAL: No FreeSurfer directory found. "
        "Set 'freesurfer' in config.json to the subject's FreeSurfer directory.",
        "error"
    )
    create_product_json(report_items)
    sys.exit(1)

if not os.path.isdir(os.path.join(subjects_dir, subject)):
    add_info_to_product(
        report_items,
        f"FATAL: FreeSurfer subject directory not found: "
        f"{os.path.join(subjects_dir, subject)}",
        "error"
    )
    create_product_json(report_items)
    sys.exit(1)

add_info_to_product(report_items, f"Subject: {subject}", "info")
os.environ["SUBJECTS_DIR"] = subjects_dir

# == CHECK DIGITIZATION POINTS ==
if not info['dig']:
    add_info_to_product(
        report_items,
        "FATAL: No digitization points found. "
        "Coregistration requires digitized fiducials (nasion, LPA, RPA).",
        "error"
    )
    create_product_json(report_items)
    sys.exit(1)

fid_count = sum(1 for d in info['dig'] if d['kind'] == FIFF.FIFFV_POINT_CARDINAL)
hsp_count = sum(1 for d in info['dig']
                if d['kind'] in (FIFF.FIFFV_POINT_HPI, FIFF.FIFFV_POINT_EXTRA))

add_info_to_product(
    report_items,
    f"Digitization: {fid_count} fiducials, {hsp_count} head shape points",
    "info"
)

if fid_count < 3:
    add_info_to_product(
        report_items,
        f"FATAL: Only {fid_count} fiducial point(s) found (need 3: nasion, LPA, RPA).",
        "error"
    )
    create_product_json(report_items)
    sys.exit(1)

if hsp_count == 0:
    add_info_to_product(
        report_items,
        "No head shape points — running fiducials-only fit (less accurate). "
        "For better results, digitize head shape points.",
        "warning"
    )

# == PREPARE 3D BACKEND (offscreen, via shared brainlife_utils helper) ==
use_3d = False
use_meg = modality in ('meg', 'meeg')
use_eeg = modality in ('eeg', 'meeg')

try:
    setup_offscreen_3d_backend()
    use_3d = True
except Exception as e:
    add_info_to_product(report_items, f"3D alignment plots unavailable: {e}", "warning")

# app-bem-v2 (watershed) always runs before coreg, so outer_skin + brain are guaranteed.
# outer_skin = scalp at 40% opacity (see-through); brain = inner_skull solid inside.
# Valid names — scalp: 'head'/'outer_skin'; skull: 'outer_skull', 'inner_skull'/'brain'
_plot_surface = {'outer_skin': 0.4, 'brain': 1.0}
add_info_to_product(report_items, "Plot surfaces: outer_skin (40% opacity) + brain (inner_skull)", "info")

plot_kwargs = dict(
    subject=subject, subjects_dir=subjects_dir,
    surfaces=_plot_surface,
    dig=True,
    meg='sensors' if use_meg else [],
    eeg='projected' if use_eeg else [],
    coord_frame='head',
    show_axes=True,
)


# Camera positions for the 4-view alignment screenshots saved at each
# coregistration step below (front/back/left/right around the head).
_alignment_views = [
    ("Front", [(0,  0.6, 0),  (0,0,0), (0,0,1)]),
    ("Back",  [(0, -0.6, 0),  (0,0,0), (0,0,1)]),
    ("Left",  [(-0.6, 0, 0),  (0,0,0), (0,0,1)]),
    ("Right", [( 0.6, 0, 0),  (0,0,0), (0,0,1)]),
]

# == COREGISTRATION ==
# fiducials: 'auto' | 'estimated' | JSON dict string e.g. '{"nasion":[0,0.1,0],"lpa":[-0.07,0,0],"rpa":[0.07,0,0]}'
fiducials_raw = config.get('fiducials') or 'auto'
import json
try:
    fiducials = json.loads(fiducials_raw)  # parse dict if JSON string provided
except (TypeError, ValueError, json.JSONDecodeError):
    fiducials = fiducials_raw  # keep as string ('auto', 'estimated')

try:
    coreg = mne.coreg.Coregistration(
        info, subject, subjects_dir,
        fiducials=fiducials,
        on_defects='warn'  # don't crash on minor surface defects
    )
    # Report which MRI surface is used for coregistration fitting
    _coreg_surf = getattr(coreg, '_bem_surf', None) or getattr(coreg, 'bem_surf', None)
    _coreg_surf_name = (os.path.basename(_coreg_surf.get('file', ''))
                        if isinstance(_coreg_surf, dict) else str(_coreg_surf))
    if not _coreg_surf_name or _coreg_surf_name == 'None':
        # Fall back to checking which surface file exists on disk
        _candidates = [
            (f'{subject}-head-dense.fif', 'bem'),
            (f'{subject}-head.fif', 'bem'),
            ('lh.seghead', 'surf'),
            ('lh.pial', 'surf'),
        ]
        for _fname, _subdir in _candidates:
            if os.path.isfile(os.path.join(subjects_dir, subject, _subdir, _fname)):
                _coreg_surf_name = _fname
                break
        else:
            _coreg_surf_name = 'unknown'
    add_info_to_product(report_items, f"Coreg fitting surface: {_coreg_surf_name}", "info")
except Exception as e:
    add_info_to_product(report_items, f"FATAL: Could not initialise coregistration: {e}", "error")
    create_product_json(report_items)
    sys.exit(1)

try:
    # Step 1: initial state (saved to file only, not product.json)
    step_name, step_label, step_add_to_product = 'coreg_01_initial', '1. Initial (before fit)', False
    if use_3d:
        view_files = []
        try:
            fig = mne.viz.plot_alignment(info, trans=coreg.trans, **plot_kwargs)
            # show() sets _first_time=False so render() actually re-renders
            fig.plotter.show(auto_close=False)
            for view_label, cam in _alignment_views:
                try:
                    fig.plotter.camera_position = cam
                    fig.plotter.render()
                    fpath = os.path.join("out_figs", f"{step_name}_{view_label.lower()}.png")
                    fig.plotter.screenshot(fpath)
                    view_files.append((view_label, fpath))
                except Exception as e:
                    add_info_to_product(report_items,
                                        f"Could not render {step_label} {view_label}: {e}", "warning")
            try:
                fig.plotter.close()
            except Exception:
                pass
        except Exception as e:
            add_info_to_product(report_items, f"Could not create alignment figure: {e}", "warning")
            view_files = []
        if view_files:
            # Tile all views into a 2x2 grid (for report + optionally product.json)
            tiled_path = os.path.join("out_figs", f"{step_name}_tiled.png")
            try:
                fig_mpl, axes = plt.subplots(2, 2, figsize=(12, 9))
                fig_mpl.suptitle(step_label, fontsize=13, fontweight="bold")
                for ax, (view_label, fpath) in zip(axes.flat, view_files):
                    ax.imshow(plt.imread(fpath))
                    ax.set_title(view_label, fontsize=10)
                    ax.axis("off")
                # blank unused panels if fewer than 4 views succeeded
                for ax in axes.flat[len(view_files):]:
                    ax.axis("off")
                plt.tight_layout()
                fig_mpl.savefig(tiled_path, dpi=120, bbox_inches="tight")
                plt.close(fig_mpl)
            except Exception as e:
                add_info_to_product(report_items, f"Could not tile {step_label} views: {e}", "warning")
                tiled_path = None
            # Add tiled image + individual views to report
            if tiled_path and os.path.isfile(tiled_path):
                report.add_image(tiled_path, title=step_label)
            for view_label, fpath in view_files:
                report.add_image(fpath, title=f"{step_label} — {view_label}")
            # product.json gets the tiled image (or front view fallback)
            if step_add_to_product:
                product_img = tiled_path if (tiled_path and os.path.isfile(tiled_path)) else view_files[0][1]
                add_image_to_product(report_items, step_label, filepath=product_img)

    # Step 2: coarse fit using fiducials
    coreg.fit_fiducials(verbose=True)
    add_info_to_product(report_items, "Fiducials fit complete", "info")

    step_name, step_label, step_add_to_product = 'coreg_02_fiducials', '2. After fiducials fit', False
    if use_3d:
        view_files = []
        try:
            fig = mne.viz.plot_alignment(info, trans=coreg.trans, **plot_kwargs)
            # show() sets _first_time=False so render() actually re-renders
            fig.plotter.show(auto_close=False)
            for view_label, cam in _alignment_views:
                try:
                    fig.plotter.camera_position = cam
                    fig.plotter.render()
                    fpath = os.path.join("out_figs", f"{step_name}_{view_label.lower()}.png")
                    fig.plotter.screenshot(fpath)
                    view_files.append((view_label, fpath))
                except Exception as e:
                    add_info_to_product(report_items,
                                        f"Could not render {step_label} {view_label}: {e}", "warning")
            try:
                fig.plotter.close()
            except Exception:
                pass
        except Exception as e:
            add_info_to_product(report_items, f"Could not create alignment figure: {e}", "warning")
            view_files = []
        if view_files:
            # Tile all views into a 2x2 grid (for report + optionally product.json)
            tiled_path = os.path.join("out_figs", f"{step_name}_tiled.png")
            try:
                fig_mpl, axes = plt.subplots(2, 2, figsize=(12, 9))
                fig_mpl.suptitle(step_label, fontsize=13, fontweight="bold")
                for ax, (view_label, fpath) in zip(axes.flat, view_files):
                    ax.imshow(plt.imread(fpath))
                    ax.set_title(view_label, fontsize=10)
                    ax.axis("off")
                # blank unused panels if fewer than 4 views succeeded
                for ax in axes.flat[len(view_files):]:
                    ax.axis("off")
                plt.tight_layout()
                fig_mpl.savefig(tiled_path, dpi=120, bbox_inches="tight")
                plt.close(fig_mpl)
            except Exception as e:
                add_info_to_product(report_items, f"Could not tile {step_label} views: {e}", "warning")
                tiled_path = None
            # Add tiled image + individual views to report
            if tiled_path and os.path.isfile(tiled_path):
                report.add_image(tiled_path, title=step_label)
            for view_label, fpath in view_files:
                report.add_image(fpath, title=f"{step_label} — {view_label}")
            # product.json gets the tiled image (or front view fallback)
            if step_add_to_product:
                product_img = tiled_path if (tiled_path and os.path.isfile(tiled_path)) else view_files[0][1]
                add_image_to_product(report_items, step_label, filepath=product_img)

    # Step 3: ICP refinement — only if head shape points available
    if hsp_count > 0:
        icp_iter_1   = int(config.get('icp_iterations_1') or 6)
        icp_iter_2   = int(config.get('icp_iterations_2') or 20)
        nasion_w1    = float(config.get('nasion_weight_1') or 2.0)
        nasion_w2    = float(config.get('nasion_weight_2') or 10.0)
        omit_dist_mm = float(config.get('omit_distance_mm') or 5.0)

        coreg.fit_icp(n_iterations=icp_iter_1, nasion_weight=nasion_w1, verbose=True)

        # file only (not added to product.json)
        step_name, step_label, step_add_to_product = 'coreg_03_icp1', f'3. After ICP ({icp_iter_1} iterations)', False
        if use_3d:
            view_files = []
            try:
                fig = mne.viz.plot_alignment(info, trans=coreg.trans, **plot_kwargs)
                # show() sets _first_time=False so render() actually re-renders
                fig.plotter.show(auto_close=False)
                for view_label, cam in _alignment_views:
                    try:
                        fig.plotter.camera_position = cam
                        fig.plotter.render()
                        fpath = os.path.join("out_figs", f"{step_name}_{view_label.lower()}.png")
                        fig.plotter.screenshot(fpath)
                        view_files.append((view_label, fpath))
                    except Exception as e:
                        add_info_to_product(report_items,
                                            f"Could not render {step_label} {view_label}: {e}", "warning")
                try:
                    fig.plotter.close()
                except Exception:
                    pass
            except Exception as e:
                add_info_to_product(report_items, f"Could not create alignment figure: {e}", "warning")
                view_files = []
            if view_files:
                # Tile all views into a 2x2 grid (for report + optionally product.json)
                tiled_path = os.path.join("out_figs", f"{step_name}_tiled.png")
                try:
                    fig_mpl, axes = plt.subplots(2, 2, figsize=(12, 9))
                    fig_mpl.suptitle(step_label, fontsize=13, fontweight="bold")
                    for ax, (view_label, fpath) in zip(axes.flat, view_files):
                        ax.imshow(plt.imread(fpath))
                        ax.set_title(view_label, fontsize=10)
                        ax.axis("off")
                    # blank unused panels if fewer than 4 views succeeded
                    for ax in axes.flat[len(view_files):]:
                        ax.axis("off")
                    plt.tight_layout()
                    fig_mpl.savefig(tiled_path, dpi=120, bbox_inches="tight")
                    plt.close(fig_mpl)
                except Exception as e:
                    add_info_to_product(report_items, f"Could not tile {step_label} views: {e}", "warning")
                    tiled_path = None
                # Add tiled image + individual views to report
                if tiled_path and os.path.isfile(tiled_path):
                    report.add_image(tiled_path, title=step_label)
                for view_label, fpath in view_files:
                    report.add_image(fpath, title=f"{step_label} — {view_label}")
                # product.json gets the tiled image (or front view fallback)
                if step_add_to_product:
                    product_img = tiled_path if (tiled_path and os.path.isfile(tiled_path)) else view_files[0][1]
                    add_image_to_product(report_items, step_label, filepath=product_img)

        coreg.omit_head_shape_points(distance=omit_dist_mm / 1000)
        coreg.fit_icp(n_iterations=icp_iter_2, nasion_weight=nasion_w2, verbose=True)
        add_info_to_product(
            report_items,
            f"ICP refinement complete ({icp_iter_1} + {icp_iter_2} iterations, "
            f"omit > {omit_dist_mm} mm)",
            "info"
        )

    # Step 4: final result — only this one goes into product.json
    step_name, step_label, step_add_to_product = 'coreg_04_final', 'Final alignment', True
    if use_3d:
        view_files = []
        try:
            fig = mne.viz.plot_alignment(info, trans=coreg.trans, **plot_kwargs)
            # show() sets _first_time=False so render() actually re-renders
            fig.plotter.show(auto_close=False)
            for view_label, cam in _alignment_views:
                try:
                    fig.plotter.camera_position = cam
                    fig.plotter.render()
                    fpath = os.path.join("out_figs", f"{step_name}_{view_label.lower()}.png")
                    fig.plotter.screenshot(fpath)
                    view_files.append((view_label, fpath))
                except Exception as e:
                    add_info_to_product(report_items,
                                        f"Could not render {step_label} {view_label}: {e}", "warning")
            try:
                fig.plotter.close()
            except Exception:
                pass
        except Exception as e:
            add_info_to_product(report_items, f"Could not create alignment figure: {e}", "warning")
            view_files = []
        if view_files:
            # Tile all views into a 2x2 grid (for report + optionally product.json)
            tiled_path = os.path.join("out_figs", f"{step_name}_tiled.png")
            try:
                fig_mpl, axes = plt.subplots(2, 2, figsize=(12, 9))
                fig_mpl.suptitle(step_label, fontsize=13, fontweight="bold")
                for ax, (view_label, fpath) in zip(axes.flat, view_files):
                    ax.imshow(plt.imread(fpath))
                    ax.set_title(view_label, fontsize=10)
                    ax.axis("off")
                # blank unused panels if fewer than 4 views succeeded
                for ax in axes.flat[len(view_files):]:
                    ax.axis("off")
                plt.tight_layout()
                fig_mpl.savefig(tiled_path, dpi=120, bbox_inches="tight")
                plt.close(fig_mpl)
            except Exception as e:
                add_info_to_product(report_items, f"Could not tile {step_label} views: {e}", "warning")
                tiled_path = None
            # Add tiled image + individual views to report
            if tiled_path and os.path.isfile(tiled_path):
                report.add_image(tiled_path, title=step_label)
            for view_label, fpath in view_files:
                report.add_image(fpath, title=f"{step_label} — {view_label}")
            # product.json gets the tiled image (or front view fallback)
            if step_add_to_product:
                product_img = tiled_path if (tiled_path and os.path.isfile(tiled_path)) else view_files[0][1]
                add_image_to_product(report_items, step_label, filepath=product_img)

except Exception as e:
    add_info_to_product(report_items, f"FATAL: Coregistration fitting failed: {e}", "error")
    create_product_json(report_items)
    sys.exit(1)

# == QUALITY CHECK ==
dists = np.array([])
try:
    dists  = coreg.compute_dig_mri_distances() * 1e3  # mm
    mean_d = np.mean(dists)
    min_d  = np.min(dists)
    max_d  = np.max(dists)

    quality = "warning" if mean_d > 5.0 else "info"
    add_info_to_product(
        report_items,
        f"Fit quality — HSP ↔ MRI surface distances:\n"
        f"  mean: {mean_d:.2f} mm | min: {min_d:.2f} mm | max: {max_d:.2f} mm",
        quality
    )
    if mean_d > 5.0:
        add_info_to_product(
            report_items,
            f"Mean distance {mean_d:.1f} mm > 5 mm. "
            "Check digitization quality and inspect the alignment plots.",
            "warning"
        )
except Exception as e:
    add_info_to_product(report_items, f"Could not compute fit distances: {e}", "warning")

# == SAVE trans.fif ==
trans_path = os.path.join('out_dir', 'trans.fif')
try:
    mne.write_trans(trans_path, coreg.trans, overwrite=True)
    add_info_to_product(report_items, "trans.fif saved successfully", "info")
except Exception as e:
    add_info_to_product(report_items, f"FATAL: Could not save trans.fif: {e}", "error")
    create_product_json(report_items)
    sys.exit(1)

# == FIGURES (always-available matplotlib plots) ==

# Distance histogram
if len(dists) > 0:
    try:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(dists, bins=30, color='steelblue', edgecolor='white', alpha=0.8)
        ax.axvline(np.mean(dists), color='red', linestyle='--',
                   label=f'Mean: {np.mean(dists):.2f} mm')
        ax.axvline(5.0, color='orange', linestyle=':', alpha=0.8, label='5 mm threshold')
        ax.set_xlabel('Distance (mm)')
        ax.set_ylabel('Count')
        ax.set_title('Head Shape Point to MRI Surface Distances')
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        fig_path = os.path.join('out_figs', 'coreg_distances.png')
        plt.savefig(fig_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        add_image_to_product(report_items, 'Fit Distances Histogram', filepath=fig_path)
    except Exception as e:
        add_info_to_product(report_items, f"Could not plot distances: {e}", "warning")

# Sensor topomap (saved to file for report only)
try:
    fig_sensors = mne.viz.plot_sensors(info, show_names=False, show=False)
    fig_path = os.path.join('out_figs', 'coreg_sensors.png')
    fig_sensors.savefig(fig_path, dpi=100, bbox_inches='tight')
    plt.close(fig_sensors)
except Exception as e:
    add_info_to_product(report_items, f"Could not plot sensors: {e}", "warning")

# == SAVE REPORT ==
# Add static matplotlib figures (distances + sensors)
for fname, title in [('coreg_distances', 'Fit distances histogram'),
                     ('coreg_sensors',   'Sensor topomap')]:
    fpath = os.path.join('out_figs', fname + '.png')
    if os.path.isfile(fpath):
        report.add_image(fpath, title=title)
report.save(os.path.join('out_dir_report', 'report.html'), overwrite=True)

add_info_to_product(report_items, "Coregistration completed successfully", "success")
create_product_json(report_items)
print("Done.")
