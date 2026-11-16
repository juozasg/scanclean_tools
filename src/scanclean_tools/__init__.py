"""scanclean_tools - phone photos of journal pages -> flat greyscale scans.

Commands (defined in pyproject.toml):
  scanclean          all steps            -> scanclean_tools.run_all
  scanclean-dewarp   step 1, geometry     -> scanclean_tools.step1_dewarp
  scanclean-tone     step 2, colour       -> scanclean_tools.step2_tone
  scanclean-grid     step 2b, grid warp   -> scanclean_tools.step2b_grid
  scanclean-pdf      step 3, PDF          -> scanclean_tools.step3_pdf
  dewarp-tool        click-to-mark hints  -> scanclean_tools.dewarp_tool
  grid-tool          hints for step 2b    -> scanclean_tools.grid_tool

The building blocks live in scanclean_tools.scanlib.
"""
