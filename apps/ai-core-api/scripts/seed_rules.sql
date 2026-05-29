-- Seed rules and facts

INSERT INTO ai_rules (title, body, scope_type, scope_value, status, priority) VALUES
('Odoo Artifact Policy',
 'Intermediate, debug, OCR, parsed, or scratch artifacts must be stored in AI Platform artifact storage. Attach to Odoo only when: the user explicitly requested that file; or the file is a final business deliverable; or the workflow specifically defines it as an Odoo attachment. Raw CSV, OCR text, JSON, or debug files must not be attached to Odoo unless explicitly requested.',
 'system', 'odoo', 'active', 10),
('Odoo Chatter Policy',
 'Do not post raw OCR, CSV, JSON, or long tabular data into Odoo chatter unless the user explicitly asks for that exact raw content to be posted. Default chatter output should be a short human-readable summary.',
 'system', 'odoo', 'active', 10),
('User Identity Policy',
 'Direct user-triggered actions must use the requesting user''s connected account wherever possible. Scheduled or autonomous automations must use service identities and record created_by, owner, service_identity, job_id.',
 'global', NULL, 'active', 5),
('Cosmetic Connection Credit Note Reconciliation',
 'Use STK-CODE to match customer product code. Use GROSS as unit price. Group quantities across all PDFs. Store OCR outputs in AI artifacts, not Odoo. Attach only final workbook unless raw exports requested.',
 'workflow', NULL, 'active', 20)
ON CONFLICT DO NOTHING;

INSERT INTO ai_company_facts (key, value, category, confidence) VALUES
('default_currency', 'ZAR', 'finance', 'high'),
('odoo_primary_db', 'Lots Lots More Production', 'systems', 'high'),
('ai_platform_region', 'southafricanorth', 'infrastructure', 'high')
ON CONFLICT (key) DO NOTHING;
