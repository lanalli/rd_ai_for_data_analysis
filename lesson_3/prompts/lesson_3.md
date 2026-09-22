Analyze payments/payments.csv for data structure, quality, consistency, and reliability risks.  
Do not modify the file.  
Identify anything that could make downstream reporting, aggregation, joining/merging, calculations, or business conclusions unreliable. Check for missing/duplicate records, inconsistent formats or values, incorrect data types, ambiguous fields, broken relationships, suspicious/outlier records, conflicting information, and potential misleading interpretations.  
Group findings into:  
1. Existing problems — confirmed data issues.  
2. Potential issues — anomalies or risks requiring validation.  
3. Business/reporting threats — issues that could materially distort KPIs, trends, revenue/payment analysis, or business decisions.  
4. Data integration risks — issues that may prevent reliable aggregation, joins, or merging with other datasets.  
For each finding, briefly explain the evidence, affected fields/records, potential impact, and recommended validation or fix.  

------

Read reports/data_quality_audit.md and cleanup payments/payments.csv following the rules in data_quality_audit.md
Create file rules/cleaning_rules.md
For each rule describe: *the checking conditions, *the actions to take if the rules are violated, *changed columns, *validation check


-----------
Based on this project's work please create a short CLAUDE.md. Please fix:
*the structure of the project, *profiling and cleaning order, *the rules to work with raw data, *the requirement to confirm before removing/deleting anything, *requirements for scripts and validation, *prohibited actions


----

turn this workflow to the reusable skill 'validate-dataset'. Skill should: *accept path to tables, *run profiling, *execute rules from the folder /rules, *create validation report, *do not change source data without validation, *separate confirmed issues from assumptions. Show the structure of the skill and the way of launching it
