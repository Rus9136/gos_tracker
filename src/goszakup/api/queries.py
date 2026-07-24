"""GraphQL-запросы к OWS v3.

Наблюдения recon (tests/fixtures/api/NOTES.md): фильтры-списки чисел работают
и как массив, и как одиночное значение; `amount: [X]` означает «от X»;
серверный `kato` — только точные полные коды (регион фильтруем клиентски);
пагинация — аргумент `after` + `extensions.pageInfo.lastId`.
"""

# Листинг лотов — эквивалент /ru/search/lots. Корень Lots: статусы, сумма и
# customerBin фильтруются на сервере; регион (префикс КАТО), год и вид
# предмета — клиентски в mapping/ApiSource.
LISTING_QUERY = """
query($f: LotsFiltersInput, $limit: Int, $after: Int) {
  Lots(filter: $f, limit: $limit, after: $after) {
    id
    lotNumber
    refLotStatusId
    amount
    count
    nameRu
    descriptionRu
    customerBin
    customerNameRu
    trdBuyId
    trdBuyNumberAnno
    plnPointKatoList
    RefTradeMethods { nameRu }
    Plans { RefEnstru { code nameRu } }
    TrdBuy { publishDate refSubjectTypeId kato }
  }
}
"""

# Деталь объявления — эквивалент 5 HTML-табов одним запросом (winners и
# contracts в API-источнике не заполняются, их дотягивает HTML-фолбэк).
DETAIL_QUERY = """
query($f: TrdBuyFiltersInput) {
  TrdBuy(filter: $f, limit: 1) {
    id
    numberAnno
    nameRu
    totalSum
    countLots
    orgBin
    orgNameRu
    publishDate
    endDate
    refBuyStatusId
    RefTradeMethods { nameRu }
    RefSubjectType { nameRu }
    RefTypeTrade { nameRu }
    Lots {
      id
      lotNumber
      refLotStatusId
      amount
      count
      nameRu
      descriptionRu
      customerBin
      customerNameRu
      Plans { RefEnstru { code nameRu } }
    }
    Files { filePath originalName nameRu }
  }
}
"""
