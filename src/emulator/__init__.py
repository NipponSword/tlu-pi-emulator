"""Raspberry Pi 5 test bench for the momiji-tlu trigger board.

pulser     : fake PMT, N pulses at a given rate on PIN_PMT
fake_altai : fake DUT, raises busy on PIN_BUSY for each trg_o on PIN_TRG
loopback   : Pi-only self test (jumper PIN_PMT -> PIN_TRG)
"""
__version__ = "0.1.0"
