## SEMA-TABANLI SORGULAMA KURALLARI

1. Node label'larini SEMADAN al - Tahmin ETME!
2. Iliski adlarini SEMADAN al - Uydurma!
3. Property isimlerini SEMADAN al - Varsayma!
4. Iliski yonlerini SEMADAN al - Ters yazma!

## WHERE SIRALAMA (EN KRITIK)

Neo4j'de WHERE sadece HEMEN ONCESINDEKI MATCH/OPTIONAL MATCH'e uygulanir!
OPTIONAL MATCH'ten SONRA WHERE yazarsan FILTRE CALISMAZ, TUM SATIRLAR DONER!

DOGRU - Filtreleri OPTIONAL MATCH'ten ONCE yaz:
```cypher
MATCH (a:A)-[:REL]->(b:B)
WHERE a.name IN ['X']
MATCH (b)-[:REL2]->(c:C)
WHERE c.type = 'Y'
OPTIONAL MATCH (c)-[:DATE]->(d:Date)
RETURN ...
```

DOGRU - WITH ile ayir:
```cypher
MATCH (a:A)-[:REL]->(b:B)-[:REL2]->(c:C)
WHERE a.name IN ['X'] AND c.type = 'Y'
WITH a, b, c
OPTIONAL MATCH (c)-[:DATE]->(d:Date)
RETURN ...
```

## STRING ARAMASI

KESIF: `apoc.text.clean()` ile fuzzy ara
ANA SORGU: Kesiften bulunan EXACT deger kullan

## ILISKI YONU

Semada (A)-[:REL]->(B) ise:
- DOGRU: MATCH (a:A)-[:REL]->(b:B)
- YANLIS: MATCH (b:B)-[:REL]->(a:A)

## AGGREGATE

Toplam: SUM(n.field) | Ortalama: AVG(n.field) | Sayi: COUNT(DISTINCT n)
