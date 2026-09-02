# coreg

Automated MEG/EEG coregistration of sensor positions to MRI anatomy.

Coregistration aligns the digitized head shape and fiducials (nasion, LPA, RPA) to the FreeSurfer MRI surface. The output transform (`trans.fif`) is required to compute the forward model.

## Inputs

| Datatype | Description |
|----------|-------------|
| `neuro/freesurfer` | FreeSurfer subject directory (output of recon-all) |
| `neuro/meg` or `neuro/eeg` | Raw or epochs FIF file (must contain digitization points) |

## Outputs

| Datatype | File | Description |
|----------|------|-------------|
| `meeg/mne/trans` | `trans.fif` | MRI-to-head coordinate transform (consumed by app-forward-v2) |
| `report/html` | `report.html` | Coregistration quality report |

## Parameters

| Parameter | Default | Options | Description |
|-----------|---------|---------|-------------|
| `fiducials` | `auto` | `auto`, `estimated`, JSON dict | Fiducial positions. `auto` = use subject file if available, otherwise estimate from MRI. `estimated` = always estimate. Or provide a JSON dict: `{"nasion":[0,0.1,0],"lpa":[-0.07,0,0],"rpa":[0.07,0,0]}` (meters). |
| `icp_iterations_1` | `6` | integer | Number of ICP iterations in first pass (coarse). |
| `icp_iterations_2` | `20` | integer | Number of ICP iterations in second pass (fine, after outlier removal). |
| `nasion_weight_1` | `2.0` | float | Nasion weight during first ICP pass. |
| `nasion_weight_2` | `10.0` | float | Nasion weight during second ICP pass. |
| `omit_distance_mm` | `5.0` | float | Head shape points farther than this (mm) from the MRI surface are excluded before the second ICP pass. |

## Algorithm

1. **Fiducials fit** — coarse alignment using nasion, LPA, RPA
2. **ICP refinement** (first pass) — iterative closest point using all head shape points
3. **Outlier removal** — head shape points beyond `omit_distance_mm` from surface are excluded
4. **ICP refinement** (second pass) — final fit with remaining points and higher nasion weight

ICP steps are skipped if no head shape points are present (fiducials-only fit, with warning).

## Quality Metrics

- HSP-to-MRI surface distances: mean, min, max (mm)
- Warning if mean distance > 5 mm
- Histogram of point distances in report

## Pipeline Position

```
app-freesurfer-v2
      │
      ├──→ app-coreg-v2   (trans.fif)        ← this app
      │
      ├──→ app-bem-v2     (bem-sol.fif)
      │
      └──→ app-source-space-v2 (source_space-src.fif)
                │
                └──→ app-forward-v2
```

## Container

`docker://aunnikri642/app-freesurfer-mne-source-recon`

Contains FreeSurfer 7.3.2 and MNE-Python 1.11.
