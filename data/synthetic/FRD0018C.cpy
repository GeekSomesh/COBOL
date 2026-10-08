    *> CHANNEL O=ONLINE B=BRANCH A=ATM
    05 CH-CD              PIC X.
    *> CARD PRESENT Y/N
    05 WS-CPF             PIC X.
    *> TRANSACTION AMOUNT
    05 T-AMT1             PIC 9(7)V99.
    05 PROC-DATE          PIC 9(8).
    05 PROC-DATE-R REDEFINES PROC-DATE.
       10 PROC-DATE-YYYY  PIC 9(4).
       10 PROC-DATE-MM    PIC 9(2).
       10 PROC-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(2).
