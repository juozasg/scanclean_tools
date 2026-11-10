# scanclean — phone photos of journal pages → flat greyscale "scans"

Three independent steps, each a script of its own, plus a driver:

| step | script | does | reads → writes |
|---|---|---|---|
| 1 | `step1_dewarp.py` | **geometry only**: un-warps the page, colours untouched | `photos/` → `photos/flat/` |
| 2 | `step2_tone.py` | **colour only**: light, shadows, paper tint → greyscale; trim | `photos/flat/` → `photos/clean/` |
| 3 | `step3_pdf.py` | bundles page images into a PDF | `photos/clean/` → `photos/clean.pdf` |
| all | `run_all.sh` | runs 1 → 2 → 3 (bash) | |
| all | `scanclean.py` | the same in Python, for shells without bash | |
| – | `hint_tool.py` | click-to-mark hints for pages step 1 gets wrong | `photos/<page>.hints.json` |

Install once: `pip install -r requirements.txt`

## Workflow

1. **Run everything**

   ```
   ./run_all.sh path/to/photos
   ```

   About 7 s per 12 MP photo. On Windows run it from Git Bash or WSL, or use
   `python scanclean.py path\to\photos --pdf article.pdf`.

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
   python hint_tool.py path/to/photos --only 156
   ```

   | key | then click | meaning |
   |---|---|---|
   | `r` | 4 corners of a printed frame (any order) | fastest hint: 2 horizontal + 2 vertical lines |
   | `h` | 2+ points along a line, `Enter` | this line is horizontal on the page |
   | `v` | 2+ points along a line, `Enter` | this line is vertical on the page |
   | `x` | 2 corners of a box | ignore automatic guides in here (messy figure) |
   | `c` | 2 corners of a box | crop the page to this area |

   `g` shows the automatic guides, mouse wheel zooms, right-drag pans, `u` undoes,
   `n`/`p` next/previous photo, `q` quits. Follow a bent line with several clicks; a
   straight-looking line needs only its two ends.

5. **Redo only those pages** (the PDF is rebuilt from all pages)

   ```
   ./run_all.sh path/to/photos 156
   ```

6. **Change the look without redoing geometry**

   ```
   STEPS="2 3" TONE_OPTS="--white 0.82" ./run_all.sh path/to/photos
   ```

7. Optional text layer: `ocrmypdf -l rus+deu clean.pdf clean_ocr.pdf`.

## `run_all.sh` settings (environment variables)

| variable | default | meaning |
|---|---|---|
| `STEPS` | `"1 2 3"` | which steps to run |
| `PDF` | `PHOTO_DIR/clean.pdf` | PDF path; `PDF=none` skips step 3 |
| `DEWARP_OPTS` | | extra options for step 1, e.g. `"--format png"` |
| `TONE_OPTS` | | extra options for step 2, e.g. `"--white 0.82 --margin 6"` |
| `PDF_OPTS` | | extra options for step 3, e.g. `"--dpi 400"` |
| `PYTHON` | auto | interpreter |

## Options of the steps

**Step 1** `step1_dewarp.py SRC [-o OUT] [--only …]`

| option | default | use |
|---|---|---|
| `--format` | `jpg` (quality 95) | `png` keeps the intermediate lossless (about 5× larger) |
| `--scale` | 1.0 | output pixel density relative to the photo |
| `--focal35` | EXIF or 26 | 35 mm-equivalent focal length of the camera |
| `--no-polish` | | sheet model only |
| `--warn` | 2.5 | residual (px) above which a page is marked `CHECK` |

**Step 2** `step2_tone.py SRC [-o OUT] [--only …]`

| option | default | use |
|---|---|---|
| `--white` | 0.88 | lower (0.82) removes more show-through, higher keeps faint pencil |
| `--black` | 0.25 | higher = bolder print |
| `--margin` | 4 | white border around the print, in text heights |
| `--no-trim` | | keep the full page frame from step 1 |
| `--no-mask` | | do not whiten the area outside the page |
| `--format` | `png` | `jpg` for smaller files |

Step 2 also works on any folder of page images (e.g. flatbed scans): without `meta/` from
step 1 it estimates text height and paper area itself.

**Step 3** `step3_pdf.py SRC [-o OUT.pdf] [--dpi 300] [--only …]`

## Files between the steps

```
photos/
  167.jpg                     photo
  167.hints.json              optional, from hint_tool.py
  flat/167.jpg                step 1: flat colour page, cropped to the page
  flat/meta/167.mask.png      step 1: white = this page (not desk, gutter, facing page)
  flat/meta/167.json          step 1: text height, trim flag, fit quality
  flat/debug/167_guides.jpg   step 1: guides + fitted grid on the photo
  flat/report.json            step 1: the table, all pages
  clean/167.png               step 2: greyscale scan
  clean.pdf                   step 3
```

## Code layout

```
scanlib/util.py     file listing, image reading / writing
scanlib/paper.py    paper brightness, ink level, paper mask, text height
scanlib/guides.py   automatic guides: text lines, rules, margins
scanlib/hints.py    manual hints
scanlib/sheet.py    camera + curled-sheet model, polish field, resampling maps
scanlib/layout.py   page limits (gutter, edges), trim box, edge clean-up
scanlib/dewarp.py   step 1 as a function: dewarp_page(photo) -> image, mask, meta
scanlib/tone.py     step 2 as a function: tone_page(image, mask, ...) -> grey page
```

The step scripts only parse options and read / write files, so each stage can be replaced
or called from your own code.

## How it works

1. **Guides.** Text lines are traced as ridges of the horizontally smeared ink mask; table
   rules and figure frames / grids as continuous thin dark lines; the ends of justified
   lines give the text margins. Sideways tables are recognised. Every guide is a curve in
   the photo that must become a straight horizontal or vertical line.
2. **Sheet fit.** A pinhole camera looking at a curled sheet (a cylinder whose axis is the
   book gutter) is fitted with robust least squares: 3 rotation + 4 shape parameters.
   Guides the model cannot straighten are dropped and the fit repeated. A smooth 2-D
   polynomial field then removes the remaining 1–2 px. The photo is resampled once.
3. **Tone.** Local paper brightness → white (flat-field), local ink level → black (faint
   print near the gutter is lifted), greyscale levels, whitening outside the page.

## Hint file format

```json
{"lines":  [{"type": "h", "pts": [[x, y], [x, y]]}, {"type": "v", "pts": [[x, y], [x, y]]}],
 "rects":  [[[x, y], [x, y], [x, y], [x, y]]],
 "ignore": [[x0, y0, x1, y1]],
 "crop":   [[x, y], [x, y]],
 "auto":   true}
```

Coordinates are pixels of the photo. `"auto": false` switches the automatic guides off for
that page. A `crop` hint also switches off the trim in step 2.

## Limits

- The book is assumed to lie with its gutter roughly vertical in the photo.
- Pages that are almost only a drawing (maps) have few guides: give them an `r` hint.
- Print that is washed out in the photo (deep in the gutter) stays faint.
- Halftone photographs get the same hard contrast as text; raise `--white` and lower
  `--black` for such pages.
- Dirt at the page edge can make the trim wider than needed: use a `c` hint.
- Page sizes differ slightly between pages because the camera height differed.
