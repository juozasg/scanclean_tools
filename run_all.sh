#!/usr/bin/env bash
# run_all.sh - photos -> flat pages (geometry) -> greyscale scans (colour) -> PDF
# Thin bash driver around the three commands of this uv project; works from any folder.
#
#   ./run_all.sh PHOTO_DIR              all photos of the folder
#   ./run_all.sh PHOTO_DIR 156 164      redo only these pages (e.g. after adding hints);
#                                       the PDF is always rebuilt from all cleaned pages
#
# Optional environment variables
#   PDF=path/out.pdf        PDF to write (default: PHOTO_DIR/clean.pdf, PDF=none skips step 3)
#   DEWARP_OPTS="..."       extra options for scanclean-dewarp   e.g. "--format png"
#   TONE_OPTS="..."         extra options for scanclean-tone     e.g. "--white 0.82 --margin 6"
#   PDF_OPTS="..."          extra options for scanclean-pdf      e.g. "--dpi 400"
#   STEPS="1 2 3"           which steps to run, e.g. STEPS="2 3" to redo only colour + PDF
set -euo pipefail

if [ $# -lt 1 ]; then sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1%/}"; shift
[ -d "$SRC" ] || { echo "not a folder: $SRC" >&2; exit 1; }
ONLY=()
if [ $# -gt 0 ]; then ONLY=(--only "$@"); fi
command -v uv >/dev/null 2>&1 || { echo "uv not found (https://docs.astral.sh/uv/)" >&2; exit 1; }
RUN=(uv run --project "$HERE")          # project environment, current folder stays as it is

FLAT="$SRC/flat"
CLEAN="$SRC/clean"
PDF="${PDF:-$SRC/clean.pdf}"
STEPS="${STEPS:-1 2 3}"

for s in $STEPS; do
  case "$s" in
    1) echo "== step 1/3  geometry      $SRC -> $FLAT"
       "${RUN[@]}" scanclean-dewarp "$SRC" -o "$FLAT" ${ONLY[@]+"${ONLY[@]}"} ${DEWARP_OPTS:-} ;;
    2) echo "== step 2/3  colour        $FLAT -> $CLEAN"
       "${RUN[@]}" scanclean-tone "$FLAT" -o "$CLEAN" ${ONLY[@]+"${ONLY[@]}"} ${TONE_OPTS:-} ;;
    3) if [ "$PDF" != "none" ]; then
         echo "== step 3/3  pdf           $CLEAN -> $PDF"
         "${RUN[@]}" scanclean-pdf "$CLEAN" -o "$PDF" ${PDF_OPTS:-}
       fi ;;
    *) echo "unknown step: $s" >&2; exit 1 ;;
  esac
done
echo "done."
