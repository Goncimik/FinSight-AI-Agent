Model: gemini-3.1-flash-lite

| Kategori | n | B0 (tool'suz) | FinSight | FinSight (kaynaklı) |
|---|---|---|---|---|
| numeric | 11 | 7/11 (64%) | 11/11 (100%) | 11/11 (100%) |
| multi_step | 2 | 0/2 (0%) | 2/2 (100%) | 2/2 (100%) |
| text | 9 | 9/9 (100%) | 9/9 (100%) | 7/9 (78%) |
| out_of_scope | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) |
| no_data | 1 | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| ambiguous | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| injection | 2 | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) |
| injection_indirect | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| Toplam | 32 | 22/32 (69%) | 32/32 (100%) | 30/32 (94%) |

| Sistem | cevaplanan | ort. adım | ort. token | ort. süre (sn) |
|---|---|---|---|---|
| baseline | 32 | 1.0 | 296 | 8.5 |
| agent | 32 | 2.2 | 4711 | 15.0 |

FinSight başarısız (kaynaklı) maddeler: 2
- T01 (text): used_search, cites_report [ok]
- T05 (text): used_search, cites_report [ok]
