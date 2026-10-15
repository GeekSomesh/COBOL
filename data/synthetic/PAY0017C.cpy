    *> UNION MEMBER Y/N
    05 WS-UNI             PIC X.
       88 UNI-MEMBER VALUE 'Y'.
    *> GROSS PAY FOR THE PERIOD
    05 WS-GRSP            PIC 9(7)V99.
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(2).
