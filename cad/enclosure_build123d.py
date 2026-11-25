"""Starter build123d parametric shell importing exact coordinates from hardware/pcb_datum.py.
We will flesh out snap-fits, button flexures, and LCD bezel in the final enclosure step!
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "hardware"))
from pcb_datum import PCB

# Ready for build123d:
# from build123d import *
print(f"Loaded ProbePen PCB Datum: {PCB['length']}mm x {PCB['width']}mm x {PCB['thickness']}mm")
print(f"LCD Window X-span: {PCB['lcd_glass_x']}mm, Ask-AI Trigger at: {PCB['btn_ask_ai_xy']}mm")
