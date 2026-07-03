import json, glob
for p in sorted(glob.glob('temp/fresh_pbv2_reanalysis/agg/2*.json')):
    if '_blockers' in p or 'trace' in p:
        continue
    j = json.load(open(p, encoding='utf-8'))
    d = j['dist']
    def g(k, b):
        s = d[k][b]
        return f"n={s['n']},p50={s['p50']}"
    print(j['day'], j['ampm'],
          'mom_fresh', g('momentum_continuation_score', 'fresh'),
          'imb_fresh', g('entry_order_book_imbalance', 'fresh'),
          'board_tok', j['board_token_fresh'],
          'spread_p50', d['spread_bps']['fresh']['p50'])
