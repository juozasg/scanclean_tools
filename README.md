# scanclean-tools — phone photos of journal pages → flat greyscale "scans"

A uv project. Independent steps, each a command of its own, plus one that runs them all:

| command | step | does | reads → writes |
|---|---|---|---|
| `scanclean-dewarp` | 1 | **geometry only**: un-warps the page, colours untouched | `photos/` → `flat/` |
| `scanclean-tone` | 2 | **colour only**: light, shadows, paper tint → greyscale; trim | `flat/` → `clean/` |
| `scanclean-grid` | 2b | **grid straightening**: a smooth grid warp makes lines straight and corners square | `clean/` → `straight/` |
| `scanclean-pdf` | 3 | bundles page images into a PDF | `straight/` (or `clean/`) → `clean.pdf` |
| `scanclean` | all | runs 1 → 2 → 2b → 3 | |
| `dewarp-tool` | – | click-to-mark hints for pages step 1 gets wrong | `photos/<page>.hints.json` |
| `grid-tool` | – | click-to-mark hints for step 2b, on the clean pages | `clean/<page>.grid.json` |

`run_all.sh` is a bash driver that calls the same commands.

## Setup

```
cd scanclean_tools
uv sync                      # creates .venv, installs dependencies and the commands
```

Ways to run a command:

| where you are | how |
|---|---|
| inside the project folder | `uv run scanclean path/to/photos` |
| any folder | `uv run --project ~/code/scanclean_tools scanclean path/to/photos` |
| any folder, short form | once: `uv tool install --editable ~/code/scanclean_tools`, then just `scanclean path/to/photos` |
| bash (Git Bash / WSL) | `~/code/scanclean_tools/run_all.sh path/to/photos` |

The commands below are written in the short form; put `uv run` in front if you did not install
the tool.

## Workflow

1. **Run everything**

   ```
   scanclean path/to/photos
   ```

   About 7 s per 12 MP photo.

2. **Read the table step 1 prints**

   ```
   page text rule hint  before           after            status
    164   20   50    0  h=20.0 v=5.1     h=1.5 v=1.0      ok
   ```

   `before/after` = how far (px) the guides are from straight horizontal / vertical lines.
   After-values of 1–2 px are normal; `CHECK` means the value stayed high or few guides
   were found.

3. **Look at `flat/`** — geometry can be judged there before any colour work. Check figure
   and map pages and every `CHECK` page. `flat/debug/<page>_guides.jpg` shows what was used:
   green = text lines, blue = rules, purple = text margins, red = your hints,
   yellow grid = the fitted page.

4. **Add hints only where needed**

   ```
   dewarp-tool path/to/photos --only 156
   ```

   | key | then click | meaning |
   |---|---|---|
   | `r` | 2 opposite corners of a printed frame | fastest hint: 2 horizontal + 2 vertical lines |
   | `h` | 2+ points along a line, `Enter` or double-click | this line is horizontal on the page |
   | `v` | 2+ points along a line, `Enter` or double-click | this line is vertical on the page |
   | `x` | 2 corners of a box | ignore automatic guides in here (messy figure) |
   | `c` | 4 corners of the page (any order) | cut the page to this quad |
   | `e` | a point of any hint, then its new place | move that point (`Esc` puts it back) |

   `Enter` finishes a line, `Esc` cancels the item being marked, `u` undoes, `g` shows the
   automatic guides (solid = used, dark dashed = removed by an ignore box, magenta dashed =
   dropped by the fit as a mis-detection), mouse wheel zooms, right-drag pans, `n`/`p` next/previous photo, `q` quits.
   The list of hints at the top left shows the newest first: hover a line to highlight that
   hint, click its x to delete it, click the title (or press `l`) to fold the list.

   The right pane shows the dewarped page for the current hints and updates a moment after
   every change. Each pane has its own zoom and pan; wheel, `+`/`-`, `f` and right-drag act on
   the pane under the mouse. `s` saves the dewarped page exactly as step 1 would:
   `flat/<page>.jpg` with its `flat/meta/<page>.json` and `flat/meta/<page>.mask.png` (the mask
   is the crop quad when one is marked), all overwritten. Follow a bent line with several clicks; a
   straight-looking line needs only its two ends.

5. **Redo only those pages** (the PDF is rebuilt from all pages)

   ```
   scanclean path/to/photos --only 156
   ```

6. **Change the look without redoing geometry**

   ```
   scanclean path/to/photos --steps 2 2b 3 --white 0.82
   ```

7. **Fine straightening (step 2b)** runs by default after step 2. Step 2b prints, per page,
   how far (px) the guides are from straight lines before / after. For a page that needs
   help, mark hints on the clean page:

   ```
   grid-tool path/to/clean --only 156
   ```

   Same window and keys as dewarp-tool (`h`, `v`, `r`, `x`, `e`, `g`, `s`, `n`/`p`, `q` ...),
   with `a` = right angle (3 clicks: a point on one arm, the corner, a point on the other
   arm) instead of the crop, and `[` / `]` = straighten less / more (saved per page). In the
   `g` view the guides are coloured by how straight they end up (green < 0.1, yellow < 0.25,
   red more text heights) and the warped grid is drawn on the right pane. `s` saves
   `straight/<page>.png` exactly as step 2b would. Then rebuild the PDF:
   `scanclean path/to/photos --steps 3`. To skip the step: `--steps 1 2 3`.

8. Optional text layer: `ocrmypdf -l rus+deu clean.pdf clean_ocr.pdf`.

## `scanclean` options

| option | meaning |
|---|---|
| `--only 156 164` | only these pages (a single photo path works too) |
| `--steps 2 2b 3` | which steps to run (default `1 2 2b 3`) |
| `--pdf out.pdf` / `--no-pdf` | PDF path (default `clean.pdf` next to the photo folder) / skip step 3 |
| `--scale`, `--white`, `--black`, `--margin`, `--strength` | passed to step 1 / 2 / 2b, see below |

Step 3 uses `straight/` when step 2b runs (or ran before and step 2 does not run now), else `clean/`.

## `run_all.sh` settings (environment variables)

| variable | default | meaning |
|---|---|---|
| `STEPS` | `"1 2 2b 3"` | which steps to run |
| `PDF` | `clean.pdf` next to `PHOTO_DIR` | PDF path; `PDF=none` skips step 3 |
| `DEWARP_OPTS` | | extra options for step 1, e.g. `"--format png"` |
| `TONE_OPTS` | | extra options for step 2, e.g. `"--white 0.82 --margin 6"` |
| `GRID_OPTS` | | extra options for step 2b, e.g. `"--strength 2"` |
| `PDF_OPTS` | | extra options for step 3, e.g. `"--dpi 400"` |

## Options of the steps

**Step 1** `scanclean-dewarp SRC [-o OUT] [--only …]`

| option | default | use |
|---|---|---|
| `--format` | `jpg` (quality 95) | `png` keeps the intermediate lossless (about 5× larger) |
| `--scale` | 1.0 | output pixel density relative to the photo |
| `--focal35` | EXIF or 26 | 35 mm-equivalent focal length of the camera |
| `--no-polish` | | sheet model only |
| `--warn` | 2.5 | residual (px) above which a page is marked `CHECK` |

**Step 2** `scanclean-tone SRC [-o OUT] [--only …]`

| option | default | use |
|---|---|---|
| `--white` | 0.88 | lower (0.82) removes more show-through, higher keeps faint pencil |
| `--black` | 0.25 | higher = bolder print |
| `--margin` | 4 | white border around the print, in text heights |
| `--deblur` | 2.5 | sharpen regions that are out of focus (blur is measured per region; sharp parts are left alone); higher is stronger, 0 = off |
| `--show-through` | 110 | remove faint patches (show-through from the back) that never get darker than this grey level; higher removes more, 0 keeps everything (faint pencil notes) |
| `--keep-edges` | off | by default, dark streaks along the image edges left by gutter or page-edge shadow (long and thin along an edge, or filling a corner) are removed, only within the border strip set by `--edge-band`; this flag keeps them |
| `--edge-band` | 0.15 | width of the border strip (fraction of page width / height) where edge shadow is looked for; nothing further in is changed |
| `--no-adaptive` | off | by default, gutter zones where the print is fainter than in the centre (found per page, reported as "faded gutter") get their own white/black points so faded letters stay readable; this flag uses the same levels everywhere |
| `--no-trim` | | keep the full page frame from step 1 |
| `--format` | `png` | `jpg` for smaller files |

Step 2 also works on any folder of page images (e.g. flatbed scans): without `meta/` from
step 1 it estimates text height and paper area itself.

**Step 2b** `scanclean-grid SRC [-o OUT] [--only …]`

| option | default | meaning |
|---|---|---|
| `--strength` | 1.0 | how hard to straighten: higher bends more freely, lower stays closer to the input; a page's `grid-tool` hints can set their own |
| `--cell` | 4 | grid cell size in text heights (smaller follows finer ripples) |
| `--format` | `png` | `jpg` for smaller files |

**Step 3** `scanclean-pdf SRC [-o OUT.pdf] [--dpi 300] [--only …]`

Every page gets the same paper size and margins: pages are scaled slightly (at most
`--max-scale`, default 15 %) so the printed area has the same size throughout, and the printed area is
centred left to right at the same distance from the top, so a partial last page sits at the
top with empty paper below. `--margin` (default 0.08) sets the white border; `--as-is`
embeds the images unchanged, each at its own size.

## Files between the steps

```
photos/167.jpg                photo
photos/167.hints.json         optional, from dewarp-tool
flat/167.jpg                  step 1: flat colour page, cropped to the page
flat/meta/167.mask.png        step 1: white = this page (not desk, gutter, facing page)
flat/meta/167.json            step 1: text height, trim flag, fit quality
flat/debug/167_guides.jpg     step 1: guides + fitted grid on the photo
flat/report.json              step 1: the table, all pages
clean/167.png                 step 2: greyscale scan
clean/167.grid.json           optional, from grid-tool
straight/167.png              step 2b: straightened page
straight/meta/167.json        step 2b: guide error before / after, grid, node shifts
clean.pdf                     step 3
```

`flat/`, `clean/`, `straight/` and `clean.pdf` are made next to the photo folder, not inside it.

## Project layout

```
pyproject.toml                 dependencies, commands ([project.scripts]), build backend
uv.lock                        exact versions (written by uv)
run_all.sh                     bash driver
src/scanclean_tools/           the package (import name: scanclean_tools)
  run_all.py                   scanclean
  step1_dewarp.py              scanclean-dewarp
  step2_tone.py                scanclean-tone
  step2b_grid.py               scanclean-grid
  step3_pdf.py                 scanclean-pdf
  dewarp_tool.py               dewarp-tool (its editor is the base of grid-tool)
  grid_tool.py                 grid-tool
  scanlib/util.py              file listing, image reading / writing
  scanlib/paper.py             paper brightness, ink level, paper mask, text height
  scanlib/guides.py            automatic guides: text lines, rules, margins
  scanlib/hints.py             manual hints
  scanlib/sheet.py             camera + curled-sheet model, polish field, resampling maps
  scanlib/layout.py            page limits (gutter, edges), trim box, edge clean-up
  scanlib/dewarp.py            step 1 as a function: dewarp_page(photo) -> image, mask, meta
  scanlib/tone.py              step 2 as a function: tone_page(image, ...) -> grey page
  scanlib/grid.py              step 2b as a function: straighten(grey page, hints) -> page, meta
```

The step modules only parse options and read / write files; from your own code use
`from scanclean_tools.scanlib.dewarp import dewarp_page` and
`from scanclean_tools.scanlib.tone import tone_page`.

Add a dependency with `uv add <package>`; after changing `[project.scripts]` run `uv sync`.

## How it works

1. **Guides.** Text lines are traced as ridges of the horizontally smeared ink mask; table
   rules and figure frames / grids as continuous thin dark lines; the ends of justified
   lines give the text margins. Sideways tables are recognised. Every guide is a curve in
   the photo that must become a straight horizontal or vertical line.
2. **Sheet fit.** A pinhole camera looking at a curled sheet (a cylinder whose axis is the
   book gutter) is fitted with robust least squares: 3 rotation + 4 shape parameters.
   Guides the model cannot straighten are dropped and the fit repeated. A smooth 2-D
   polynomial field then removes the remaining 1–2 px. The photo is resampled once.
3. **Tone.** Out-of-focus regions are found from how soft the edges of the print are, and
   sharpened by denoising plus a deconvolution matched to their blur. Then local paper brightness → white (flat-field), local ink level → black (faint
   print near the gutter is lifted), greyscale levels (in gutter zones where the print has faded compared with the page centre,
   the white and black points move towards the faint print), then show-through removal: faint
   patches with no dark core anywhere are turned white. Dark shadow streaks running along the image edges are
   removed too (only within the outer 15% of the page, `--edge-band`). Step 2 assumes each page image shows
   only paper, so it uses no mask.
4. **Grid straightening.** No camera model: a lattice of control nodes (one every 4 text
   heights) whose 2-D shifts are blended by cubic B-splines into a smooth warp. Guides are
   found again on the clean page (text lines, rules, margins) plus the `grid-tool` hints.
   The node shifts minimise, by sparse weighted least squares: the spread of each
   horizontal guide's points in y (and of each vertical guide's in x) - straight and level
   in one term; 90° at rule crossings and at right-angle hints; equal spacing of neighbouring
   text lines (weak); bending of the grid; stretch and shear of the grid cells; and a weak
   pull towards no shift, so empty areas stay as they are. Right angles are linearised
   (Gauss-Newton) and a Huber weight tames bad points. A coarse 4 × 5 grid first finds
   automatic guides that cannot be straightened (and guides tilted or bent far more than a
   page from step 1 can be) and drops them; then the fine grid is solved. If a grid cell
   would fold over, the smoothing is raised. The output is resampled once (inverse map by
   fixed-point iteration) and resampling haze is turned back to white.

## Hint file format

```json
{"lines":  [{"type": "h", "pts": [[x, y], [x, y]]}, {"type": "v", "pts": [[x, y], [x, y]]}],
 "rects":  [[[x, y], [x, y], [x, y], [x, y]]],
 "ignore": [[x0, y0, x1, y1]],
 "crop":   [[x, y], [x, y], [x, y], [x, y]],
 "auto":   true}
```

Coordinates are pixels of the photo. `"auto": false` switches the automatic guides off for
that page. A `crop` hint (4 corners; older files with 2 opposite corners still work) also switches off
the trim in step 2: everything outside the quad becomes white.

Grid hints (`clean/<page>.grid.json`, clean page pixels) use the same `lines`, `rects` and
`ignore`, plus `"angles": [[[x, y], [x, y], [x, y]]]` (arm point, corner, arm point) and
`"strength": 1.0`.

## Limits

- The book is assumed to lie with its gutter roughly vertical in the photo.
- Pages that are almost only a drawing (maps) have few guides: give them an `r` hint.
- Print that is washed out in the photo (deep in the gutter) stays faint.
- Halftone photographs get the same hard contrast as text; raise `--white` and lower
  `--black` for such pages.
- Dirt at the page edge can make the trim wider than needed: use a `c` hint.
- Page sizes differ slightly between pages because the camera height differed.
- Step 2b can only see a distortion that bends a guide: a whole text line shifted
  sideways, or lines moved evenly up / down, stays (unless a vertical guide or the line
  spacing shows it). A curve in a figure that is detected as a line can be straightened
  wrongly: use an `x` box in grid-tool. Each resample softens the text a little.
