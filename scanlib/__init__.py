"""scanlib - building blocks for turning page photos into flat greyscale scans.

  util    file listing, image I/O
  paper   paper brightness, ink level, paper mask, text height
  guides  automatic guides (text lines, rules, margins)
  hints   manual hints from hint_tool.py
  sheet   camera + curled-sheet model, polish field, resampling maps
  layout  page limits (gutter, edges), crop box, edge clean-up
  dewarp  step 1: photo -> flat colour page (+ page mask, metadata)
  tone    step 2: flat colour page -> greyscale scan
"""
