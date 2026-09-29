-- Deal Rescue AI schema version 1 (M2), captured before the M3 migration. Test fixture only.
CREATE TABLE commitments (
	id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	deal_id VARCHAR(40) NOT NULL, 
	source_interaction_id VARCHAR(40), 
	description VARCHAR(1000) NOT NULL, 
	owner_party VARCHAR(16) NOT NULL, 
	owner_name VARCHAR(200), 
	due_date DATE, 
	status VARCHAR(16) NOT NULL, 
	completed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_commitments_deal_same_customer FOREIGN KEY(deal_id, customer_id) REFERENCES deals (id, customer_id), 
	CONSTRAINT fk_commitments_interaction_same_customer FOREIGN KEY(source_interaction_id, customer_id) REFERENCES interactions (id, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE customers (
	id VARCHAR(40) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	industry VARCHAR(100), 
	notes TEXT, 
	is_synthetic BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE deals (
	id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	stage VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	value_minor INTEGER, 
	currency VARCHAR(3), 
	expected_close_date DATE, 
	owner_name VARCHAR(200), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_deals_id_customer UNIQUE (id, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE interaction_participants (
	interaction_id VARCHAR(40) NOT NULL, 
	stakeholder_id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (interaction_id, stakeholder_id), 
	CONSTRAINT fk_participants_interaction_same_customer FOREIGN KEY(interaction_id, customer_id) REFERENCES interactions (id, customer_id) ON DELETE CASCADE, 
	CONSTRAINT fk_participants_stakeholder_same_customer FOREIGN KEY(stakeholder_id, customer_id) REFERENCES stakeholders (id, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE interactions (
	id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	deal_id VARCHAR(40), 
	occurred_at DATETIME NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	title VARCHAR(200), 
	notes TEXT NOT NULL, 
	idempotency_key VARCHAR(100), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_interactions_id_customer UNIQUE (id, customer_id), 
	CONSTRAINT uq_interactions_idempotency UNIQUE (customer_id, idempotency_key), 
	CONSTRAINT fk_interactions_deal_same_customer FOREIGN KEY(deal_id, customer_id) REFERENCES deals (id, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE memory_source_refs (
	id VARCHAR(40) NOT NULL, 
	memory_write_id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	bank_id VARCHAR(64) NOT NULL, 
	memory_id VARCHAR(64) NOT NULL, 
	document_id VARCHAR(120) NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	source_id VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_memory_source_refs_memory UNIQUE (bank_id, memory_id), 
	CONSTRAINT fk_memory_source_refs_write FOREIGN KEY(memory_write_id) REFERENCES memory_writes (id) ON DELETE CASCADE, 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE memory_writes (
	id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	source_id VARCHAR(40) NOT NULL, 
	bank_id VARCHAR(64) NOT NULL, 
	document_id VARCHAR(120) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	attempts INTEGER NOT NULL, 
	content_hash VARCHAR(64), 
	stored_hash VARCHAR(64), 
	last_error VARCHAR(500), 
	last_attempt_at DATETIME, 
	stored_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_memory_writes_source UNIQUE (source_type, source_id), 
	CONSTRAINT uq_memory_writes_document UNIQUE (bank_id, document_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE stakeholders (
	id VARCHAR(40) NOT NULL, 
	customer_id VARCHAR(40) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	role VARCHAR(200), 
	influence VARCHAR(16) NOT NULL, 
	email VARCHAR(254), 
	priorities JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_stakeholders_id_customer UNIQUE (id, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);
CREATE INDEX ix_commitments_customer_due ON commitments (customer_id, due_date);
CREATE INDEX ix_commitments_deal_status ON commitments (deal_id, status);
CREATE INDEX ix_commitments_source_interaction ON commitments (source_interaction_id);
CREATE INDEX ix_customers_name ON customers (name);
CREATE INDEX ix_deals_customer_id ON deals (customer_id);
CREATE INDEX ix_deals_customer_status ON deals (customer_id, status);
CREATE INDEX ix_interactions_customer_occurred ON interactions (customer_id, occurred_at);
CREATE INDEX ix_interactions_deal_occurred ON interactions (deal_id, occurred_at);
CREATE INDEX ix_memory_source_refs_source ON memory_source_refs (source_type, source_id);
CREATE INDEX ix_memory_writes_customer_status ON memory_writes (customer_id, status);
CREATE INDEX ix_participants_stakeholder ON interaction_participants (stakeholder_id);
CREATE INDEX ix_stakeholders_customer_name ON stakeholders (customer_id, name);
