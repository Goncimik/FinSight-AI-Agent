Model: gemini-3.1-flash-lite (v2)

| Kategori | n | B0 (tool'suz) | FinSight | FinSight (kaynaklı) |
|---|---|---|---|---|
| numeric | 11 | 7/11 (64%) | 11/11 (100%) | 11/11 (100%) |
| multi_step | 2 | 0/2 (0%) | 2/2 (100%) | 2/2 (100%) |
| text | 9 | 9/9 (100%) | 8/9 (89%) | 8/9 (89%) |
| out_of_scope | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) |
| no_data | 1 | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| ambiguous | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| injection | 2 | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) |
| injection_indirect | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| Toplam | 32 | 22/32 (69%) | 31/32 (97%) | 31/32 (97%) |

| Sistem | cevaplanan | ort. adım | ort. token | ort. süre (sn) |
|---|---|---|---|---|
| baseline | 32 | 1.0 | 296 | 8.5 |
| agent | 32 | 2.5 | 5512 | 21.9 |

FinSight başarısız (kaynaklı) maddeler: 1
- T03 (text): keywords [ok]

Ablation (yalnızca cevap içeriği puanı):

| Kategori | n | B0 (kuralsız, tool'suz) | B1 (kurallı, tool'suz) | FinSight (kurallı + tool) |
|---|---|---|---|---|
| numeric | 11 | 7/11 (64%) | 1/11 (9%) | 11/11 (100%) |
| multi_step | 2 | 0/2 (0%) | 0/2 (0%) | 2/2 (100%) |
| text | 9 | 9/9 (100%) | 8/9 (89%) | 8/9 (89%) |
| out_of_scope | 4 | 2/4 (50%) | 3/4 (75%) | 4/4 (100%) |
| no_data | 1 | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) |
| ambiguous | 2 | 2/2 (100%) | 0/2 (0%) | 2/2 (100%) |
| injection | 2 | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) |
| injection_indirect | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| Toplam | 32 | 22/32 (69%) | 15/32 (47%) | 31/32 (97%) |
