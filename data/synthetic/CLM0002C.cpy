    *> CLAIM TYPE A=AUTO H=HOME M=MEDICAL
    05 :PFX:-L-TY            PIC X.
    *> POLICE REPORT FILED Y/N
    05 :PFX:-PR-FLG          PIC X.
    *> CLAIMED AMOUNT
    05 :PFX:-WS-CLAM         PIC 9(7)V99.
    *> CLAIMS IN LAST 3 YEARS
    05 :PFX:-P-CC            PIC 9(2).
    05 :PFX:-RUN-DATE        PIC 9(8).
    05 :PFX:-RUN-DATE-R REDEFINES :PFX:-RUN-DATE.
       10 :PFX:-RUN-DATE-YYYY  PIC 9(4).
       10 :PFX:-RUN-DATE-MM    PIC 9(2).
       10 :PFX:-RUN-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(4).
