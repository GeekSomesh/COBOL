    *> CUSTOMER AGE IN YEARS
    05 :PFX:-WS-CAGE         PIC 9(3).
    05 :PFX:-T-AMT1          PIC 9(7)V99.
    05 :PFX:-WS-CTRY         PIC X(2).
    05 :PFX:-POST-DATE       PIC 9(8).
    05 :PFX:-POST-DATE-R REDEFINES :PFX:-POST-DATE.
       10 :PFX:-POST-DATE-YYYY  PIC 9(4).
       10 :PFX:-POST-DATE-MM    PIC 9(2).
       10 :PFX:-POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
