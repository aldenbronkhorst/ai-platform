-- Seed tools
INSERT INTO ai_tools (name, display_name, description, target_system, input_schema, output_schema, version, status, requires_approval) VALUES
('odoo.search_read', 'Odoo Search Read', 'Search and read records from Odoo', 'odoo', '{"model": "string", "domain": "list", "fields": "list"}', '{"records": "list"}', '1.0.0', 'active', 'false'),
('odoo.execute_kw', 'Odoo Execute KW', 'Execute any Odoo model method', 'odoo', '{"model": "string", "method": "string", "args": "list", "kwargs": "dict"}', '{"result": "any"}', '1.0.0', 'active', 'false'),
('odoo.attachment_ocr', 'Odoo Attachment OCR', 'OCR an attachment from Odoo', 'odoo', '{"attachment_id": "integer"}', '{"text": "string"}', '1.0.0', 'active', 'false'),
('odoo.attach_artifact', 'Odoo Attach Artifact', 'Attach a file to an Odoo record', 'odoo', '{"model": "string", "record_id": "integer", "artifact_id": "string"}', '{"attachment_id": "integer"}', '1.0.0', 'active', 'false'),
('github.create_pr', 'GitHub Create PR', 'Create a pull request on GitHub', 'github', '{"repo": "string", "title": "string", "body": "string", "head": "string", "base": "string"}', '{"pr_url": "string"}', '1.0.0', 'active', 'false'),
('github.search_repo', 'GitHub Search Repo', 'Search within a GitHub repository', 'github', '{"repo": "string", "query": "string"}', '{"results": "list"}', '1.0.0', 'active', 'false'),
('runner.run_python', 'Runner Run Python', 'Run a Python script in a secure runner', 'runner', '{"script": "string", "inputs": "dict"}', '{"stdout": "string", "stderr": "string", "artifacts": "list"}', '1.0.0', 'active', 'false'),
('ai.save_artifact', 'AI Save Artifact', 'Save an artifact to AI Platform storage', 'ai-platform', '{"content": "bytes", "filename": "string", "type": "string"}', '{"artifact_id": "string", "uri": "string"}', '1.0.0', 'active', 'false'),
('ai.create_task', 'AI Create Task', 'Create a task in the AI Platform', 'ai-platform', '{"title": "string", "description": "string", "owner": "string"}', '{"task_id": "string"}', '1.0.0', 'active', 'false')
ON CONFLICT (name) DO NOTHING;
