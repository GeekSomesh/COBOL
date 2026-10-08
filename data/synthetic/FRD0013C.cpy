    05 CUST-AGE           PIC 9(3).
    *> TRANSACTION AMOUNT
    05 TXN-AMT            PIC 9(7)V99.
    05 CNTRY-CODE         PIC X(2).
    05 RUN-DATE           PIC 9(8).
    05 RUN-DATE-R REDEFINES RUN-DATE.
       10 RUN-DATE-YYYY  PIC 9(4).
       10 RUN-DATE-MM    PIC 9(2).
       10 RUN-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(4).
