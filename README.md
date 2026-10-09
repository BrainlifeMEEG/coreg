# Automated MEG/EEG Coregistration

[![Run on Brainlife.io](https://img.shields.io/badge/Brainlife-bl.app.957-blue.svg)](https://doi.org/10.25663/brainlife.app.957)

## Description

Automated coregistration of MEG/EEG sensor positions to FreeSurfer MRI anatomy, using
[`mne.coreg.Coregistration`](https://mne.tools/stable/generated/mne.coreg.Coregistration.html).

Coregistration aligns the digitized head shape and fiducials (nasion, LPA, RPA) to the
FreeSurfer MRI surface in two stages: a coarse fiducials-only fit (`fit_fiducials`), followed by
iterative closest point (ICP) refinement (`fit_icp`) using the head shape points. The resulting
transform (`trans.fif`) is required to compute the forward model.

The app generates:
- `trans.fif` — the MRI-to-head coordinate transform
- `report.html` — a coregistration quality report with alignment views at each fitting step and a fit-distance histogram

## Inputs

- **`freesurfer`** (`neuro/freesurfer`): FreeSurfer subject directory (output of recon-all) (required)
- **`raw`** (`neuro/meeg/mne/raw`): continuous raw FIF file with sensor digitization points (nasion, LPA, RPA, and optionally head shape points) (required if `epochs` is not provided)
- **`epochs`** (`neuro/meeg/mne/epochs`): epoched FIF file with sensor digitization points (required if `raw` is not provided)

On brainlife.io, these three inputs are written into the app's `config.json` under the keys
`output`, `mne` and `epo` respectively; `main.py` accepts either that naming or the
`freesurfer`/`raw`/`epochs` keys used in this repo's `config.json` for local testing.

## Outputs

- `out_dir/trans.fif` (`neuro/meeg/mne/trans`): MRI-to-head coordinate transform (consumed by downstream forward-model apps)
- `out_dir_report/report.html` (`report/html`): coregistration quality report (alignment views per fitting step, fit-distance histogram, sensor topomap)

## Configuration Parameters

| key | type | default | description |
|-----|------|---------|-------------|
| `subjects_dir` | string (path) | `null` | Optional override for the parent directory of the FreeSurfer subject folder. Only needed if it cannot be derived from `freesurfer`. |
| `subject` | string | `null` | Optional override for the FreeSurfer subject name. Only needed if it cannot be derived from `freesurfer`. |
| `fiducials` | string | `"auto"` | Fiducial positions. `auto` uses the subject's fiducials file if present, otherwise estimates from the MRI; `estimated` always estimates from the template; or provide a JSON dict `{"nasion":[x,y,z],"lpa":[...],"rpa":[...]}` in meters. |
| `icp_iterations_1` | integer | `6` | Number of ICP iterations in the first (coarse) refinement pass. |
| `icp_iterations_2` | integer | `20` | Number of ICP iterations in the second (fine) refinement pass, run after outlier removal. |
| `nasion_weight_1` | float | `2.0` | Weight applied to the nasion point during the first ICP pass. |
| `nasion_weight_2` | float | `10.0` | Weight applied to the nasion point during the second ICP pass. |
| `omit_distance_mm` | float | `5.0` | Head-shape points farther than this distance (mm) from the MRI surface are excluded before the second ICP pass. |

## Usage

### Running on Brainlife.io

1. Select a FreeSurfer subject directory (output of recon-all) as the `freesurfer` input.
2. Select a raw or epochs FIF file containing sensor digitization as the `raw` or `epochs` input.
3. Set `fiducials` and the ICP parameters as needed (the defaults work well for most datasets).
4. Submit the process.
5. Review the alignment views and fit-distance histogram in `report.html`, and check that `trans.fif` was produced for use by the forward-model app.

### Local Testing

```bash
# Edit config.json to point "freesurfer" and "raw" (or "epochs") at real files, then:
python main.py
```

## Technical Details

### Algorithm

1. **Fiducials fit** — coarse alignment using nasion, LPA, RPA (`coreg.fit_fiducials`)
2. **ICP refinement** (first pass) — iterative closest point using all head shape points (`coreg.fit_icp`)
3. **Outlier removal** — head shape points beyond `omit_distance_mm` from the MRI surface are excluded (`coreg.omit_head_shape_points`)
4. **ICP refinement** (second pass) — final fit with the remaining points and a higher nasion weight

ICP steps are skipped if no head shape points are present (fiducials-only fit, with a warning noted in the report).

### Quality Metrics

- HSP-to-MRI surface distances: mean, min, max (mm), via `coreg.compute_dig_mri_distances()`
- A warning is raised if the mean distance exceeds 5 mm
- A histogram of point distances is included in the report

### Container

`docker://brainlifemeeg/mne-freesurfer:1.12.1-7.4.1` (FreeSurfer 7.4.1 + MNE-Python 1.12.1), as invoked by the `main` script.

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

## Authors

- obVdo (https://github.com/obVdo)
- Maximilien Chaumon (https://github.com/dnacombo)

## Citations

- Hayashi, S., Caron, B.A., Heinsfeld, A.S. et al. brainlife.io: a decentralized and open-source cloud platform to support neuroscience research. Nat Methods 21, 809–813 (2024). https://doi.org/10.1038/s41592-024-02237-2
- Gramfort, A. et al. MEG and EEG data analysis with MNE-Python. Front. Neurosci. 7, 267 (2013). https://doi.org/10.3389/fnins.2013.00267

## Funding Acknowledgement

brainlife.io is publicly funded and for the sustainability of the project it is helpful to acknowledge the use of the platform. We kindly ask that you acknowledge the funding below in your code and publications.

[![NSF-BCS-1734853](https://img.shields.io/badge/NSF_BCS-1734853-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1734853)
[![NSF-BCS-1636893](https://img.shields.io/badge/NSF_BCS-1636893-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1636893)
[![NSF-ACI-1916518](https://img.shields.io/badge/NSF_ACI-1916518-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1916518)
[![NSF-IIS-1912270](https://img.shields.io/badge/NSF_IIS-1912270-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1912270)
[![NIH-NIBIB-R01EB029272](https://img.shields.io/badge/NIH_NIBIB-R01EB029272-green.svg)](https://grantome.com/grant/NIH/R01-EB029272-01)
[![NIH-NIBIB-R01EB030896](https://img.shields.io/badge/NIH_NIBIB-R01EB030896-green.svg)](https://grantome.com/grant/NIH/R01-EB030896-01)

## License

Copyright (c) 2026 MEEG Brainlife team. Licensed under AGPL-3.0, see [license.txt](license.txt).
