    05 WS-TRM             PIC 9(3).
       88 TRM-BAND-1-6 VALUE 1 THRU 6.
       88 TRM-BAND-7-12 VALUE 7 THRU 12.
       88 TRM-BAND-13-36 VALUE 13 THRU 36.
    05 RUN-DATE           PIC 9(8).
    05 RUN-DATE-R REDEFINES RUN-DATE.
       10 RUN-DATE-YYYY  PIC 9(4).
       10 RUN-DATE-MM    PIC 9(2).
       10 RUN-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(2).
