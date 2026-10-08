    *> TRANSACTIONS IN LAST 24 HOURS
    05 WS-TCNT            PIC 9(3).
    *> TRANSACTION AMOUNT
    05 TA-AMT             PIC 9(7)V99.
    05 A-OD               PIC 9(5).
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
