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

# Пункты годового плана (plans_sync_actor). Фильтры по датам у корня Plans
# не работают вовсе (любое непустое «от» отдаёт 0 записей), поэтому окно
# задаётся курсором: выдача идёт по id DESC, а водяной знак — max(point_id)
# в нашей БД (см. jobs/plans.py). Ref*-объекты дают человекочитаемые имена
# сразу, без похода в /v3/refs.
PLANS_QUERY = """
query($f: PlansFiltersInput, $limit: Int, $after: Int) {
  Plans(filter: $f, limit: $limit, after: $after) {
    id
    rootrecordId
    plnPointYear
    subjectBiin
    subjectNameRu
    nameRu
    descRu
    extraDescRu
    refEnstruCode
    amount
    price
    count
    refMonthsId
    refTradeMethodsId
    refPlnPointStatusId
    prepayment
    supplyDateRu
    isActive
    dateCreate
    timestamp
    RefUnits { nameRu }
    RefSubjectType { nameRu }
    RefTradeMethods { nameRu }
    RefPlnPointStatus { nameRu }
    RefEnstru { code nameRu }
    RefFinsource { nameRu }
    RefBudgetType { nameRu }
    PlanActs { planActNumber dateApproved }
    PlansKato { refKatoCode fullDeliveryPlaceNameRu }
    PlansSpec { ekrbCode ekrbNameRu fkrbProgramCode fkrbProgramNameRu abpCode abpNameRu amount }
  }
}
"""

# Заявки поставщиков по одному объявлению (bids_sync_actor). Фильтр TrdApp
# принимает только скалярный buyId (массив, в отличие от TrdBuy.id, не берёт),
# поэтому запрос — на объявление. Цены живут в AppLots: price за единицу,
# amount — сумма, discountPrice — цена с условной скидкой (у ЗЦП обычно 0).
BIDS_QUERY = """
query($f: TrdAppFiltersInput, $limit: Int, $after: Int) {
  TrdApp(filter: $f, limit: $limit, after: $after) {
    id
    supplierBinIin
    dateApply
    Supplier { bin nameRu }
    AppLots {
      id
      lotId
      price
      amount
      discountValue
      discountPrice
      RefAppStatus { nameRu }
    }
  }
}
"""

# Карточка участника из реестра (jobs/contacts.py): контакты поставщика.
# ЮЛ ищутся по bin, ИП — только по iin (12-значный идентификатор ИП — это
# ИИН, фильтр bin его не находит; проверено вживую 2026-07-28). Покрытие
# неполное — часть ИП в реестре отсутствует вовсе.
SUBJECT_QUERY = """
query($f: SubjectFiltersInput) {
  Subjects(filter: $f) {
    bin
    iin
    nameRu
    email
    phone
    website
    Address { addressType address phone }
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
