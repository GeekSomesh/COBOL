    *> CLAIM TYPE A=AUTO H=HOME M=MEDICAL
    05 :PFX:-L-TY            PIC X.
       88 L-AUTO VALUE 'A'.
    *> POLICE REPORT FILED Y/N
    05 :PFX:-P-RP            PIC X.
       88 P-IS-N VALUE 'N'.
    *> CLAIMED AMOUNT
    05 :PFX:-WS-CLAM         PIC 9(7)V99.
    *> CLAIMS IN LAST 3 YEARS
    05 :PFX:-PC-CT           PIC 9(2).
