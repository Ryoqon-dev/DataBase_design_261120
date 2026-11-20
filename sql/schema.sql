CREATE TABLE ingredient (
    ingredient_id NUMBER(10) CONSTRAINT pk_ingredient PRIMARY KEY,
    name VARCHAR2(50 CHAR) CONSTRAINT nn_ingredient_name NOT NULL,
    category VARCHAR2(20 CHAR) CONSTRAINT nn_category NOT NULL,
    unit VARCHAR2(2 CHAR) CONSTRAINT nn_unit NOT NULL,
    CONSTRAINT uq_ingredient_name UNIQUE (name),
    CONSTRAINT ck_ingredient_id CHECK (ingredient_id > 0),
    CONSTRAINT ck_unit CHECK (unit IN ('g', 'mL', '개'))
)
/
CREATE TABLE inventory (
    inventory_id NUMBER(10) CONSTRAINT pk_inventory PRIMARY KEY,
    ingredient_id NUMBER(10) CONSTRAINT nn_stock_ingredient NOT NULL,
    registered_on DATE DEFAULT TRUNC(CURRENT_DATE) CONSTRAINT nn_registered_on NOT NULL,
    remaining_qty NUMBER(10,3) CONSTRAINT nn_stock_qty NOT NULL,
    storage VARCHAR2(2 CHAR) CONSTRAINT nn_storage NOT NULL,
    expiry_date DATE,
    CONSTRAINT fk_stock_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredient (ingredient_id),
    CONSTRAINT ck_stock_id CHECK (inventory_id > 0),
    CONSTRAINT ck_stock_qty CHECK (remaining_qty >= 0),
    CONSTRAINT ck_storage CHECK (storage IN ('실온', '냉장', '냉동')),
    CONSTRAINT ck_registered_day CHECK (registered_on = TRUNC(registered_on)),
    CONSTRAINT ck_expiry_day CHECK (expiry_date = TRUNC(expiry_date))
)
/
CREATE TABLE recipe (
    recipe_id NUMBER(10) CONSTRAINT pk_recipe PRIMARY KEY,
    name VARCHAR2(80 CHAR) CONSTRAINT nn_recipe_name NOT NULL,
    base_servings NUMBER(10) CONSTRAINT nn_base_servings NOT NULL,
    instructions CLOB CONSTRAINT nn_instructions NOT NULL,
    cooking_minutes NUMBER(10) CONSTRAINT nn_cooking_minutes NOT NULL,
    CONSTRAINT ck_recipe_id CHECK (recipe_id > 0),
    CONSTRAINT ck_base_servings CHECK (base_servings > 0),
    CONSTRAINT ck_cooking_minutes CHECK (cooking_minutes > 0)
)
/
CREATE TABLE recipe_ingredient (
    recipe_id NUMBER(10) CONSTRAINT nn_item_recipe NOT NULL,
    ingredient_id NUMBER(10) CONSTRAINT nn_item_ingredient NOT NULL,
    required_qty NUMBER(10,3) CONSTRAINT nn_required_qty NOT NULL,
    CONSTRAINT pk_recipe_ingredient PRIMARY KEY (recipe_id, ingredient_id),
    CONSTRAINT fk_item_recipe FOREIGN KEY (recipe_id) REFERENCES recipe (recipe_id),
    CONSTRAINT fk_item_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredient (ingredient_id),
    CONSTRAINT ck_required_qty CHECK (required_qty > 0)
)
/
CREATE TABLE cook_log (
    log_id NUMBER(10) CONSTRAINT pk_cook_log PRIMARY KEY,
    recipe_id NUMBER(10) CONSTRAINT nn_log_recipe NOT NULL,
    cooked_at TIMESTAMP(0) CONSTRAINT nn_cooked_at NOT NULL,
    servings NUMBER(10) CONSTRAINT nn_log_servings NOT NULL,
    memo VARCHAR2(300 CHAR),
    CONSTRAINT fk_log_recipe FOREIGN KEY (recipe_id) REFERENCES recipe (recipe_id),
    CONSTRAINT ck_log_id CHECK (log_id > 0),
    CONSTRAINT ck_log_servings CHECK (servings > 0)
)
/
CREATE SEQUENCE ingredient_seq MAXVALUE 2147483647
/
CREATE SEQUENCE inventory_seq MAXVALUE 2147483647
/
CREATE SEQUENCE recipe_seq MAXVALUE 2147483647
/
CREATE SEQUENCE cook_log_seq MAXVALUE 2147483647
/
CREATE INDEX ix_stock_ingredient ON inventory (ingredient_id)
/
CREATE INDEX ix_item_ingredient ON recipe_ingredient (ingredient_id)
/
CREATE INDEX ix_log_recipe_date ON cook_log (recipe_id, cooked_at)
/
