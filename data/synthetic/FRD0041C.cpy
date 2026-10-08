    *> TRANSACTIONS IN LAST 24 HOURS
    05 D-TC               PIC 9(3).
    05 T-AMT1             PIC 9(7)V99.
    *> DAYS SINCE ACCOUNT OPENED
    05 A-OD               PIC 9(5).
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
