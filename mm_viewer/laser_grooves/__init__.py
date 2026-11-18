"""Laser Grooves tab: the groove-depth ALGO result per wheel, with the laser
profiles the wheel assembler assigned to that wheel and those it put in Trash.

  data       scan -> wheels, algo grooves, assigned profile frames, errors   (pure)
  profiles   read / clean / align the profiles of one wheel                   (pure)
  worker     loads every wheel's profiles off the GUI thread
  vehicle    vehicle map: wheels + accumulated-profile thumbnails + depths
  detail     one wheel: aligned profiles, groove depth chart, groove table
  tab        the tab that puts them together
"""
