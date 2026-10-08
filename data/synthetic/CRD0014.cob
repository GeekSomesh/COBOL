*> ********************************************************
*> PROGRAM : CRD0014
*> PURPOSE : CREDIT APPROVAL RULES
*> AUTHOR  : D.P.
*> CHANGE LOG:
*>   1992-02-11 ORIGINAL VERSION
*>   2003-12-02 THRESHOLDS UPDATED PER AUDIT REQ 951
*> RUN FROM JCL JOB CRDNIGHT STEP040
*> ********************************************************
IDENTIFICATION DIVISION.
PROGRAM-ID. CRD0014.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-INPUT.
    *> PRIOR BANKRUPTCY Y/N
    05 BK-FLG             PIC X.
    05 WS-BKY             PIC 9(2).
    05 B-SC               PIC 9(3).
01 WS-OUT-REC.
    05 A-DC               PIC X VALUE 'D'.
    05 D-RS               PIC X(3) VALUE SPACES.
PROCEDURE DIVISION.
MAINLINE.
    ACCEPT WS-INPUT
    PERFORM 2500-BK-RULES
    DISPLAY "A-DC=" A-DC
    DISPLAY "D-RS=" D-RS
    STOP RUN.
2500-BK-RULES.
    IF BK-FLG = 'Y'
        IF WS-BKY < 5
            MOVE 'D' TO A-DC
            MOVE 'BK1' TO D-RS
        ELSE
            MOVE 'R' TO A-DC
            MOVE 'BK2' TO D-RS
        END-IF
    END-IF.
