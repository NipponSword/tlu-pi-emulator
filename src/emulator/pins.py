"""Pin assignment (BCM GPIO numbers) and Versa X4 wiring.

    Pi GPIO17 (header pin 11)  ->  X4-7  trg_i[0]   fake PMT pulse
    Pi GPIO27 (header pin 13)  <-  X4-3  trg_o      trigger to the DUT (ALTAI)
    Pi GPIO22 (header pin 15)  ->  X4-8  busy_i[0]  fake ALTAI busy
    Pi GND    (header pin 9)   --  X4-2  GND

All 3.3 V LVCMOS, connected directly (common GND).
"""
PIN_PMT = 17    # output: fake PMT pulse
PIN_TRG = 27    # input : trg_o from the TLU
PIN_BUSY = 22   # output: busy to the TLU

TLU_CLK_HZ = 160e6
