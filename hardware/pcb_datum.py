"""Single mechanical coordinate source of truth for KiCad and build123d.
Origin (0, 0) = Front tip center of PCB. +X points toward USB-C tail, Y is centered (-7.5..+7.5mm).
"""

PCB = {
    "length": 132.0,
    "width": 15.0,
    "thickness": 1.6,
    "nose_radius": 4.0,
    "tail_radius": 1.5,
    "tip_boss_xy": (4.5, 0.0),           # SMD M3 threaded probe-tip boss (0.8 mm edge clearance)
    "led_top_xy": (9.5, -5.2),           # WS2812B-2020 #1 (Y-down KiCad; clear of J2 courtyard)
    "led_bot_xy": (9.5, 5.2),            # WS2812B-2020 #2
    "afe_cutout_x": (2.0, 16.5),         # Layer-2/3 pour keepout under 1 MOhm input divider
    "afe_cutout_y": (-5.5, 5.5),
    "mount_hole_front_xy": (19.0, 0.0),  # M2 mounting hole #1 (PTH GND)
    "btn_ask_ai_xy": (34.0, 0.0),        # Index finger primary trigger
    "btn_mode_xy": (42.0, 0.0),          # Mode / Range button
    "lcd_fpc_xy": (48.5, 0.0),           # Molex 503480-0800 8P FPC (ribbon folded under LCD)
    "lcd_glass_x": (50.0, 78.0),         # FP-096H09A 13.5 x 27.95mm IPS LCD glass
    "lcd_glass_width": 13.5,
    "ms9280_xy": (83.0, 0.0),            # MS9280 SSOP-28 ADC
    "side_jst_xy": (95.0, 4.1),          # 6P JST-SH right-angle side logic pod (CAD Y-up)
    "rp2354b_xy": (109.2, 0.0),          # RP2354B QFN-80 (10x10mm)
    "mount_hole_rear_xy": (119.6, 0.0),  # M2 mounting hole #2
    "usbc_xy": (129.3, 0.0),             # 16P USB-C receptacle (1 mm tail overhang past X=132)
}
