---
name: entity-extraction
description: TSG belgelerinden cikarilacak entity ve relationship tanimlari
---

# Cikarilacak Entity'ler

Asagidaki entity tipleri filtreleme ve kumeleme icin kullanilacak.

## Company (Sirket)

Sirket bilgileri - en temel entity.

| Property        | Tip    | Aciklama         |
| --------------- | ------ | ---------------- |
| name            | string | Sirket unvani    |
| type            | enum   | AS, LTD, KOOP    |
| city            | string | Merkez sehir     |
| registration_no | string | Ticaret sicil no |
| website         | string | Web sitesi       |

## Person (Kisi)

Yonetici, ortak, yetkili kisiler.

| Property | Tip    | Aciklama |
| -------- | ------ | -------- |
| name     | string | Ad soyad |

*Gorev bilgisi HAS_ROLE relationship'inde*

## Meeting (Toplanti)

Genel kurul toplantilari.

| Property | Tip     | Aciklama                   |
| -------- | ------- | -------------------------- |
| year     | integer | Faaliyet yili (2015, 2016) |
| date     | string  | ISO tarih (YYYY-MM-DD)     |
| type     | enum    | ordinary, extraordinary    |

## Capital (Sermaye)

Sermaye bilgileri.

| Property           | Tip    | Aciklama               |
| ------------------ | ------ | ---------------------- |
| registered_ceiling | number | Kayitli sermaye tavani |
| issued             | number | Cikarilmis sermaye     |
| currency           | string | Para birimi (TRY)      |

## Gazette (Gazete)

Ticaret Sicil Gazetesi bilgileri.

| Property | Tip     | Aciklama      |
| -------- | ------- | ------------- |
| number   | integer | Gazete sayisi |
| date     | string  | ISO tarih     |

## Address (Adres)

Onemli lokasyonlar.

| Property | Tip    | Aciklama                             |
| -------- | ------ | ------------------------------------ |
| city     | string | Sehir                                |
| district | string | Ilce                                 |
| type     | enum   | headquarters, meeting_venue, factory |

## Regulation (Duzenleme)

Yasal duzenlemeler, tebligler, yonetmelikler (Resmi Gazete'de yayimlanan).

| Property | Tip     | Aciklama                         |
| -------- | ------- | -------------------------------- |
| name     | string  | Duzenleme adi                    |
| code     | string  | Kod (II-30.1, II-17.1 vb.)       |
| gazette  | integer | Resmi Gazete sayisi              |
| date     | string  | ISO tarih                        |
| issuer   | string  | Yayimlayan kurum (SPK, EPDK vb.) |

# Cikarilacak Relationship'ler

## HAS_ROLE

Kisi-Sirket arasi gorev iliskisi.

- From: Person
- To: Company
- Properties: role (chairman, vice_chairman, member, director, auditor, manager)

## HAS_SHARE

Ortaklik iliskisi.

- From: Person veya Company
- To: Company
- Properties: percentage, amount

## HAS_CAPITAL

Sirket-Sermaye iliskisi.

- From: Company
- To: Capital

## HELD_AT

Toplanti-Adres iliskisi.

- From: Meeting
- To: Address

## PUBLISHED_IN

Ilan-Gazete iliskisi.

- From: Company veya Meeting
- To: Gazette

## REFERENCES

Yasal duzenlemeye atif.

- From: Company veya Meeting
- To: Regulation
- Properties: context (atif baglami, opsiyonel)
