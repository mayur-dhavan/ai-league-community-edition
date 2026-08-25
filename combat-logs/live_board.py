"""The live AWS AI League board, transcribed from the 12:19 screenshot.

Icon legend confirmed against combat logs:
    green slime  -> c3    cyan face    -> c18   purple flower -> c1
    blue skull   -> c5    demon face   -> c2    bone helmet   -> c4
    coin -> c7   spikes -> c8   red key -> c40  red door -> c30
"""

GRID = [
    # A         B         C         D       E        F       G       H         I
    ["c3",     "normal", "c3",     "wall", "wall",  "wall", "c18",  "normal", "c18"],
    ["normal", "normal", "normal", "wall", "c7",    "wall", "wall", "normal", "normal"],
    ["wall",   "c1",     "normal", "wall", "c30",   "wall", "wall", "c1",     "wall"],
    ["c5",     "normal", "wall",   "wall", "normal","normal","normal","normal","c5"],
    ["c7",     "normal", "normal", "c8",   "normal","wall", "wall", "normal", "treasure"],
    ["c5",     "normal", "wall",   "wall", "start", "wall", "c8",   "normal", "c5"],
    ["wall",   "c1",     "wall",   "wall", "c8",    "wall", "wall", "c1",     "wall"],
    ["normal", "normal", "normal", "wall", "normal","wall", "wall", "normal", "normal"],
    ["c4",     "normal", "c4",     "wall", "c40",   "wall", "c2",   "normal", "c2"],
]
