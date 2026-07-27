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

# Договоры за окно lastUpdateDate — для contracts_sync_actor. ContractUnits
# несёт lotId (прямая привязка к нашим lots.id; lotId=0 бывает — фильтровать).
CONTRACTS_QUERY = """
query($f: ContractFiltersInput, $limit: Int, $after: Int) {
  Contract(filter: $f, limit: $limit, after: $after) {
    id
    trdBuyId
    contractNumber
    contractNumberSys
    refContractStatusId
    supplierBiin
    supplierFio
    contractSum
    faktSum
    ContractUnits { lotId itemPrice quantity totalSum refContractStatusId }
  }
}
"""

# Деталь объявления — эквивалент 5 HTML-табов одним запросом (winners и
# contracts в API-источнике не заполняются: их синкает contracts_sync_actor
# по окну lastUpdateDate, а HTML-фолбэк дотягивает при деградации API).
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
    startDate
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
