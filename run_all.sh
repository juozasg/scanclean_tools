#!/usr/bin/env bash
# run_all.sh - photos -> flat pages (geometry) -> greyscale scans (colour)
#              -> straightened pages (grid warp) -> PDF
# Thin bash driver around the commands of this uv project; works from any folder.
#
#   ./run_all.sh PHOTO_DIR              all photos of the folder
#   ./run_all.sh PHOTO_DIR 156 164      redo only these pages (e.g. after adding hints);
#                                       the PDF is always rebuilt from all cleaned pages
#
# Optional environment variables
#   PDF=path/out.pdf        PDF to write (default: clean.pdf next to PHOTO_DIR, PDF=none skips step 3)
#   DEWARP_OPTS="..."       extra options for scanclean-dewarp   e.g. "--format png"
#   TONE_OPTS="..."         extra options for scanclean-tone     e.g. "--white 0.82 --margin 6"
#   GRID_OPTS="..."         extra options for scanclean-grid     e.g. "--strength 2"
#   PDF_OPTS="..."          extra options for scanclean-pdf      e.g. "--dpi 400"
#   STEPS="1 2 2b 3"        which steps to run, e.g. STEPS="2 2b 3" to redo colour onwards;
#                           leave out 2b to skip the grid straightening (PDF from clean/)
set -euo pipefail

if [ $# -lt 1 ]; then sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1%/}"; shift
[ -d "$SRC" ] || { echo "not a folder: $SRC" >&2; exit 1; }
ONLY=()
if [ $# -gt 0 ]; then ONLY=(--only "$@"); fi
command -v uv >/dev/null 2>&1 || { echo "uv not found (https://docs.astral.sh/uv/)" >&2; exit 1; }
RUN=(uv run --project "$HERE")          # project environment, current folder stays as it is

OUT="$(cd "$SRC/.." && pwd)"           # flat/, clean/, straight/ and clean.pdf go next to the photo folder
FLAT="$OUT/flat"
CLEAN="$OUT/clean"
STRAIGHT="$OUT/straight"
PDF="${PDF:-$OUT/clean.pdf}"
STEPS="${STEPS:-1 2 2b 3}"
PAGES="$CLEAN"
# the PDF is made from straight/ when step 2b runs now, or ran before and step 2 does not run now
case " $STEPS " in
  *" 2b "*) PAGES="$STRAIGHT" ;;
  *" 2 "*) ;;
  *) [ -d "$STRAIGHT" ] && PAGES="$STRAIGHT" ;;
esac

for s in $STEPS; do
  case "$s" in
    1) echo "== step 1   geometry      $SRC -> $FLAT"
       "${RUN[@]}" scanclean-dewarp "$SRC" -o "$FLAT" ${ONLY[@]+"${ONLY[@]}"} ${DEWARP_OPTS:-} ;;
    2) echo "== step 2   colour        $FLAT -> $CLEAN"
       "${RUN[@]}" scanclean-tone "$FLAT" -o "$CLEAN" ${ONLY[@]+"${ONLY[@]}"} ${TONE_OPTS:-} ;;
    2b) echo "== step 2b  grid          $CLEAN -> $STRAIGHT"
       "${RUN[@]}" scanclean-grid "$CLEAN" -o "$STRAIGHT" ${ONLY[@]+"${ONLY[@]}"} ${GRID_OPTS:-} ;;
    3) if [ "$PDF" != "none" ]; then
         echo "== step 3    pdf           $PAGES -> $PDF"
         "${RUN[@]}" scanclean-pdf "$PAGES" -o "$PDF" ${PDF_OPTS:-}
       fi ;;
    *) echo "unknown step: $s" >&2; exit 1 ;;
  esac
done
echo "done."
