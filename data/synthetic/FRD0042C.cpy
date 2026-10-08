    *> CHANNEL O=ONLINE B=BRANCH A=ATM
    05 CH-CD              PIC X.
       88 CH-ONLINE VALUE 'O'.
    05 WS-CPF             PIC X.
       88 CPF-NOT-PRESENT VALUE 'N'.
    *> TRANSACTION AMOUNT
    05 T-AMT1             PIC 9(7)V99.
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
