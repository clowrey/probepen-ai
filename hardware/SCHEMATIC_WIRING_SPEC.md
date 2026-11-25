# KiCad 5-Sheet Pin-by-Pin Wiring Specification

## Sheet 1: `MCU_Core` (`U1` = RP2354B QFN-80)
- **Core Regulator:** `VREG_VIN` -> `+3V3_DIG` (`4.7uF` 0603 to GND). `VREG_LX` -> `L1 (3.3uH)` -> `+1V1_CORE` (`4.7uF` 0603 + `100nF` x3 to GND, wired to all `DVDD` pins). `VREG_FB` -> `+1V1_CORE`.
- **Digital Power:** All `IOVDD` pins + `USB_OTP_VDD` -> `+3V3_DIG` (`100nF` 0402 per pin). Exposed Center Pad `EP` -> `GND` (9x thermal/ground vias).
- **Analog ADC Ref:** `ADC_AVDD` -> `+3V0_REF` (output of `U5 REF3030AIDBZR` with `1uF` + `100nF` to `AGND`).
- **Clock:** `XIN` / `XOUT` (`1k` series on `XOUT`) -> `Y1 (12.000MHz)` with `15pF` C0G load caps to GND.
- **USB:** `USB_DP` / `USB_DM` -> `27R` 0402 series resistors -> `D1 (USBLC6-2SC6)` -> `J_USB`.
- **Bootloader:** `QSPI_SS_N` -> `1k` resistor -> `TP_BOOT` pad AND cathode of `1N4148WS` whose anode goes to `BTN_MODE_N` (hold `MODE` while plugging in USB-C to force UF2 ROM bootloader!).
- **GPIO Mapping:**
  - `GPIO0..7`: `MS9280` `D0..D7` (via `33R` 4x resistor arrays to damp ringing)
  - `GPIO8`: `MS9280` `CLK` (via `33R` series damping resistor)
  - `GPIO9`: `MS9280` `OTR` | `GPIO10`: `MS9280` `STBY` (`10k` pull-down)
  - `GPIO16`: `U7 (TLV3501)` Pin 4 (`OUT`)
  - `GPIO17..20`: `U8 (SN74LV4T125)` outputs `1Y..4Y` (Side 4-Ch Logic Pod)
  - `GPIO21`: `LCD_CS_N` | `GPIO22`: `LCD_SCK` | `GPIO23`: `LCD_MOSI` | `GPIO24`: `LCD_DC` | `GPIO25`: `LCD_RST_N` | `GPIO26`: `LCD_BLK_GATE_N`
  - `GPIO28`: `LED1 DIN` (`WS2812B-2020`)
  - `GPIO29`: `SW1 (BTN_ASK_AI_N)` (`10k` pull-up to `+3V3_DIG`)
  - `GPIO30`: `SW2 (BTN_MODE_N)` (`10k` pull-up to `+3V3_DIG`)
  - `GPIO31`: `AFE_GAIN_10X` -> `U6 (RS2227XN)` `IN1` & `IN2`
  - `GPIO32`: `COMP_THR_PWM` -> `10k` -> (`100nF` to GND) -> `10k` -> (`100nF` to GND) -> `COMP_THR_DAC`
  - `GPIO40 (ADC0)`: `AFE_DC_SENSE` (`10k` + `10nF` LPF from `V_ADC`)
  - `GPIO41 (ADC1)`: `COMP_THR_DAC` (feedback readback)
  - `GPIO42 (ADC2)`: `100k / 100k` divider from `+5V_VBUS`

## Sheet 2: `Power_Tree`
- `J_USB VBUS` -> `500mA` 0805 PTC fuse -> `+5V_VBUS`.
- `J_USB CC1, CC2` -> `5.1k` each to `GND`.
- `U9 (RT9013-33GB)` on `+5V_VBUS` -> `+3V3_DIG` (`10uF` in, `10uF` out).
- `FB1 (600R)` from `+5V_VBUS` -> `U10 (RT9013-33GB)` -> `+3V3_ANA` (`10uF` in, `10uF` out).
- `U5 (REF3030AIDBZR)` powered from `+3V3_ANA` -> outputs precision **`+3V0_REF` (`3.000V`)** (`1uF` out).
- `U11 (LM2776DBVR)` inverts `+3V3_ANA` -> `-3.3V_RAW` (`1uF` flying cap, `10uF` out) -> `U12 (TPS72301DBVT)` set with `R_fb1 = 49.9k`, `R_fb2 = 100k` ($V_{out} = -1.186 \times (1 + 49.9/100) = \mathbf{-1.78V}$) -> **`-1V8_ANA`**.

## Sheet 3: `AFE_and_MS9280`
- `J_TIP` -> `R1 (470k 0805)` -> `R2 (470k 0805)` -> node `V_DIV` (`1pF` 0603 C0G across R1+R2).
- `V_DIV` -> `R3 (39.2k 0.1%)` in parallel with `22pF` C0G + `3-10pF` SMD trimmer to `AGND`.
- `V_DIV` -> `D4 (BAV199)` clamping between `-1V8_ANA` (Pin 1) and `+3V3_ANA` (Pin 2), middle Pin 3 on `V_DIV`.
- `U3 (OPA356 #1)`: `V+ = +3V3_ANA`, `V- = -1V8_ANA`, `IN+ = V_DIV`, `IN- = OUT = V_BUF`.
- Reference String from `+3V0_REF`: `R7 (20.0k 0.1%)` -> `REF_1V00` (`100nF` to AGND) -> `R8 (7.50k 0.1%)` -> `REF_0V25` (`100nF` to AGND) -> `R9 (2.49k 0.1%)` -> `AGND`.
- `U6 (RS2227XN)` (`VCC = +3V3_ANA`, `GND = AGND`, `IN1=IN2=AFE_GAIN_10X`):
  - Switch 1 (`COM1` -> `U4 IN+`): `NC1 = REF_1V00` (`1x` mode), `NO1 = REF_0V25` (`10x` mode).
  - Switch 2 (`COM2` -> `U4 OUT` = `V_ADC`): `NC2` via `R5 (499R 0.1%)` to `U4 IN-`; `NO2` via `R6 (4.99k 0.1%)` to `U4 IN-` (`4.7pF` across `U4 IN-` to `V_ADC` for stability).
- `U4 (OPA356 #2)`: **`V+ = +3V3_ANA`, `V- = AGND`**, `R4 (1.00k 0.1%)` from `V_BUF` to `U4 IN-`.
- `U2 (MS9280)`: `AVDD = +3V3_ANA`, `DRVDD = +3V3_DIG`, **`SENSE` tied directly to `VREF`** (`1.0V..2.0V` span!), `DEC`/`REFT`/`REFB` bypassed per MS9280 datasheet (`100nF` + `1uF`), `AIN` fed from `V_ADC` via `33R` + `47pF` to `AGND`.

## Sheet 4 & 5: `Logic_SidePod_and_LCD`
- `U7 (TLV3501)`: `V+ = +3V3_DIG`, `V- = GND`, `IN- = V_ADC` (via `100R`), `IN+ = COMP_THR_DAC`, `SHDN = GND`, `OUT = GPIO16`.
- `J_POD (6P JST-SH)`: `1=GND`, `2=+3V3_FUSED` (`100mA` PTC), `3..6=CH0..CH3` -> `220R` series -> `D2/D3 (USBLC6)` -> `U8 (SN74LV4T125)` (`VCC=+3V3_DIG`, `OE_N=GND`) -> `GPIO17..20`.
- `J_LCD (Molex 503480-0800)` for `FP-096H09A`:
  - `Pin 1 (LEDA)`: `Q1 (AO3401A)` Drain -> `27R` -> `Pin 1` (`Q1` Source = `+3V3_DIG`, Gate = `GPIO26` w/ `47k` pull-up).
  - `Pin 2`: `GND` | `Pin 3`: `GPIO25 (RST)` | `Pin 4`: `GPIO24 (DC)` | `Pin 5`: `GPIO23 (MOSI)` | `Pin 6`: `GPIO22 (SCK)` | `Pin 7`: `+3V3_DIG` (`1uF` cap) | `Pin 8`: `GPIO21 (CS)`.
