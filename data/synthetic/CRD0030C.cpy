    05 :PFX:-WS-CSCR         PIC 9(3).
    05 :PFX:-WS-ANINC        PIC 9(7)V99.
    05 :PFX:-RUN-DATE        PIC 9(8).
    05 :PFX:-RUN-DATE-R REDEFINES :PFX:-RUN-DATE.
       10 :PFX:-RUN-DATE-YYYY  PIC 9(4).
       10 :PFX:-RUN-DATE-MM    PIC 9(2).
       10 :PFX:-RUN-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(2).
