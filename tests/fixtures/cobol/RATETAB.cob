       IDENTIFICATION DIVISION.
       PROGRAM-ID. RATETAB.
      *> Fee rates by customer tier, kept in a hard-coded table.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  RATE-TABLE-VALUES.
           05  FILLER              PIC X(9) VALUE 'G00150250'.
           05  FILLER              PIC X(9) VALUE 'S00250500'.
           05  FILLER              PIC X(9) VALUE 'B00401000'.
       01  RATE-TABLE REDEFINES RATE-TABLE-VALUES.
           05  RATE-ENTRY OCCURS 3 TIMES INDEXED BY RT-IDX.
               10  RATE-TIER       PIC X.
               10  RATE-PCT        PIC 9V999.
               10  RATE-MIN-FEE    PIC 9(2)V99.
       01  CUST-TIER               PIC X.
       01  TXN-AMT                 PIC 9(7)V99.
       01  WS-RATE                 PIC 9V999 VALUE 0.
       01  WS-MIN-FEE              PIC 9(2)V99 VALUE 0.
       01  WS-FEE                  PIC 9(7)V99 VALUE 0.
       01  WS-FOUND                PIC X VALUE 'N'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT CUST-TIER
           ACCEPT TXN-AMT
           SET RT-IDX TO 1
           SEARCH RATE-ENTRY
               AT END
                   MOVE 0.0500 TO WS-RATE
                   MOVE 25.00 TO WS-MIN-FEE
                   MOVE 'N' TO WS-FOUND
               WHEN RATE-TIER (RT-IDX) = CUST-TIER
                   MOVE RATE-PCT (RT-IDX) TO WS-RATE
                   MOVE RATE-MIN-FEE (RT-IDX) TO WS-MIN-FEE
                   MOVE 'Y' TO WS-FOUND
           END-SEARCH
           COMPUTE WS-FEE = TXN-AMT * WS-RATE
           IF WS-FEE < WS-MIN-FEE
               MOVE WS-MIN-FEE TO WS-FEE
           END-IF
           DISPLAY 'WS-RATE=' WS-RATE
           DISPLAY 'WS-MIN-FEE=' WS-MIN-FEE
           DISPLAY 'WS-FEE=' WS-FEE
           DISPLAY 'WS-FOUND=' WS-FOUND
           STOP RUN.
