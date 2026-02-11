
**DB2 12.1 LUW / Kerberos support case TS019170740 – short timeline**

| Date | Who | What |
|------|-----|------|
| **Apr 28, 2025** | Customer (Svein) | Case created. Migration DB2 9.7 → 12.1 on Azure; need Kerberos/pass-through auth due to NTLM deprecation on Windows 11. |
| **Apr 28, 2025** | IBM (Simeon) | Case assigned; entitlement team working on it. |
| **Apr 29, 2025** | Customer | Follow-up: asking expected response time. |
| **Apr 29, 2025** | IBM (Simeon) | No active DB2 LUW entitlements found; asks for contract/entitlement docs. |
| **Apr 29, 2025** | Customer | Asks if wrong customer number (216000); will upload documentation. |
| **Apr 30, 2025** | Customer | Uploads “Proof of Entitlement” and asks for verification and when to expect a reply. |
| **Apr 30, 2025** | IBM (Simeon) | PDFs don’t show DB2 LUW in the paid bundle; asks to confirm product (Db2 LUW vs other) and exact version. |
| **Apr 30, 2025** | Customer | Asks if more documentation is needed. |
| **May 02, 2025** | Customer | Uploads several entitlement/contract PDFs; asks for clarification on supported products. |
| **May 06, 2025** | IBM (Lukman) | Takes over; will investigate. |
| **May 06, 2025** | Customer | Clarifies goal: Windows SSO/pass-through with Kerberos; correct DB2 server config for Windows clients; still struggling. |
| **May 07, 2025** | IBM (Lukman) | Sends JDBC/Kerberos technote; status → Awaiting your feedback. |
| **May 07, 2025** | Customer | Says they mainly use COBOL, .NET, SQL Server linked server, QMF, etc., not JDBC; need DB2 server config for Windows SSO/pass-through. |
| **May 12, 2025** | IBM (Lukman) | Sends ODBC/Kerberos technote; status → Awaiting your feedback. |
| **May 15, 2025** | IBM (automated) | Reminder: case awaiting customer action. |
| **May 16, 2025** | Customer | COBOL client Kerberos/SSO working with external help; issues remain with DBeaver (JDBC), MFT, QMF (ODBC), OLEDB. |
| **May 26, 2025** | Customer | DB2, ODBC, OLEDB work with Kerberos; JDBC clients (Data Studio, DBeaver, MFT) fail with SQL4225N / GSSException. |
| **May 27, 2025** | IBM (Simeon) | Status → IBM is working. |
| **Jun 10, 2025** | IBM (Lukman) | After internal consultation: JDBC/GSS ticket error is a different issue; recommends opening a **new case** for JDBC so the right team can handle it; closes this case to your feedback. |
| **Jun 13, 2025** | IBM (automated) | Reminder: case awaiting your action. |
| **Jun 23, 2025** | IBM (automated) | Case closed due to no further response. |
| **Jun 23, 2025** | IBM (Lukman) | Case closed due to inactivity; can reopen within 30 days. |
| **Jul 23, 2025** | IBM | Status → Closed – Archived. |

**Summary:** Case started with entitlement checks and product confirmation, then focused on Kerberos/SSO. ODBC/OLEDB/COBOL were resolved (with your own work); JDBC (DBeaver, MFT, Data Studio) was left with a GSS/ticket error and IBM asked for a **separate case** for that. The case was later closed for inactivity and then archived.