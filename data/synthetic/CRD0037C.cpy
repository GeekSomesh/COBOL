    05 :PFX:-WS-CSCR         PIC 9(3).
    05 :PFX:-DR-RT           PIC 9V99.
    05 :PFX:-PROC-DATE       PIC 9(8).
    05 :PFX:-PROC-DATE-R REDEFINES :PFX:-PROC-DATE.
       10 :PFX:-PROC-DATE-YYYY  PIC 9(4).
       10 :PFX:-PROC-DATE-MM    PIC 9(2).
       10 :PFX:-PROC-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(2).
