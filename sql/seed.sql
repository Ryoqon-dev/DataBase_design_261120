DECLARE
    kimchi_id NUMBER;
    pork_id NUMBER;
    rice_id NUMBER;
    fried_rice_id NUMBER;
    today DATE := TRUNC(CURRENT_DATE);
BEGIN
    SELECT ingredient_seq.NEXTVAL INTO kimchi_id FROM dual;
    SELECT ingredient_seq.NEXTVAL INTO pork_id FROM dual;
    SELECT ingredient_seq.NEXTVAL INTO rice_id FROM dual;
    SELECT recipe_seq.NEXTVAL INTO fried_rice_id FROM dual;

    INSERT INTO ingredient VALUES (kimchi_id, '김치', '채소', 'g');
    INSERT INTO ingredient VALUES (pork_id, '목살', '육류', 'g');
    INSERT INTO ingredient VALUES (rice_id, '밥', '곡류', 'g');

    INSERT INTO inventory VALUES (inventory_seq.NEXTVAL, kimchi_id, today, 40, '냉장', today + 2);
    INSERT INTO inventory VALUES (inventory_seq.NEXTVAL, kimchi_id, today, 20, '냉장', NULL);
    INSERT INTO inventory VALUES (inventory_seq.NEXTVAL, kimchi_id, today - 2, 50, '냉장', today - 1);
    INSERT INTO inventory VALUES (inventory_seq.NEXTVAL, pork_id, today, 150, '냉장', today + 1);
    INSERT INTO inventory VALUES (inventory_seq.NEXTVAL, rice_id, today, 200, '냉동', today + 7);

    INSERT INTO recipe VALUES (fried_rice_id, '목살 김치볶음밥', 1, '목살과 김치를 볶은 뒤 밥을 넣어 볶는다.', 15);
    INSERT INTO recipe_ingredient VALUES (fried_rice_id, kimchi_id, 100);
    INSERT INTO recipe_ingredient VALUES (fried_rice_id, pork_id, 120);
    INSERT INTO recipe_ingredient VALUES (fried_rice_id, rice_id, 200);
END;
/
