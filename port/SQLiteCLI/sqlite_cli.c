#include "sqlite_cli.h"
#include "../../third_party/sqlite/sqlite3.h"
#include <stdio.h>

int sqliteCLI_run(char *resultOut, int resultOutCapacity) {
  sqlite3 *db = NULL;
  int rc = sqlite3_open(":memory:", &db);
  if (rc != SQLITE_OK) {
    snprintf(resultOut, resultOutCapacity, "sqlite3_open failed: %s",
             db ? sqlite3_errmsg(db) : "(no db handle)");
    sqlite3_close(db);
    return 1;
  }

  const char *setup =
      "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, value INTEGER NOT NULL);"
      "INSERT INTO items (name, value) VALUES ('alpha', 10), ('beta', 20), ('gamma', 30);";
  char *errmsg = NULL;
  rc = sqlite3_exec(db, setup, NULL, NULL, &errmsg);
  if (rc != SQLITE_OK) {
    snprintf(resultOut, resultOutCapacity, "setup failed: %s", errmsg ? errmsg : "(unknown)");
    sqlite3_free(errmsg);
    sqlite3_close(db);
    return 2;
  }

  sqlite3_stmt *stmt = NULL;
  rc = sqlite3_prepare_v2(db, "SELECT COUNT(*), SUM(value) FROM items;", -1, &stmt, NULL);
  if (rc != SQLITE_OK) {
    snprintf(resultOut, resultOutCapacity, "prepare failed: %s", sqlite3_errmsg(db));
    sqlite3_close(db);
    return 3;
  }

  int rowCount = 0;
  long long sumValue = 0;
  if (sqlite3_step(stmt) == SQLITE_ROW) {
    rowCount = sqlite3_column_int(stmt, 0);
    sumValue = sqlite3_column_int64(stmt, 1);
  }
  sqlite3_finalize(stmt);
  sqlite3_close(db);

  snprintf(resultOut, resultOutCapacity, "sqlite3 %s: %d rows, SUM(value)=%lld",
           sqlite3_libversion(), rowCount, sumValue);
  return 0;
}
