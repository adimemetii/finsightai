import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

import mysql.connector
import csv
import openpyxl


# =====================================================
# 1. LEXIMI I DATASET-IT
# =====================================================

print("Përshëndetje!")
print("Ky është FinSight AI - pastrim, vizualizim dhe parashikim financiar!")

print("""
FinSight AI synon të zhvillojë një platformë inteligjente
për analizën automatike të të dhënave financiare të bizneseve
dhe mbështetjen në marrjen e vendimeve të bazuara në të dhëna.
""")


# Marrim path-in e dataset-it nga përdoruesi
file_input = input("Shkruaj path-in e CSV/XLSX file: ")


# Kontrollojmë nëse file është CSV
if file_input.lower().endswith(".csv"):

    # Lexojmë CSV me Pandas
    df = pd.read_csv(file_input)


# Kontrollojmë nëse file është Excel
elif file_input.lower().endswith(".xlsx"):

    # Lexojmë Excel me Pandas
    df = pd.read_excel(file_input)


# Nëse file nuk është CSV ose XLSX
else:

    # Shfaqim gabimin
    print("Gabim! File duhet të jetë CSV ose XLSX.")

    # Ndërpresim programin
    exit()


# Shfaqim 5 rreshtat e parë
print("\n--- 5 rreshtat e parë ---")
print(df.head())


# Shfaqim missing values
print("\n--- Missing Values ---")
print(df.isnull().sum())


# Shfaqim statistikat
print("\n--- Statistikat ---")
print(df.describe())


# Shfaqim madhësinë e dataset-it
print("\n--- Forma e dataset-it ---")
print(df.shape)


# Shfaqim duplicates
print("\n--- Duplicates ---")
print(df.duplicated().sum())


# =====================================================
# 2. PASTRIMI
# =====================================================

# Heqim rreshtat duplicate
df.drop_duplicates(inplace=True)


# Konvertojmë Date në datetime
df["Date"] = pd.to_datetime(
    df["Date"],
    errors="coerce"
)


# Heqim rreshtat ku mungojnë këto të dhëna
df = df.dropna(
    subset=[
        "Date",
        "Amount",
        "Revenue",
        "Expenses"
    ]
)


# Shfaqim missing values pas pastrimit
print("\nMissing Values pas pastrimit:")
print(df.isnull().sum())

print("Pastrimi u përfundua!")


# =====================================================
# 3. PROFIT
# =====================================================

# Profit = Revenue - Expenses
df["Profit"] = (
    df["Revenue"] -
    df["Expenses"]
)


# =====================================================
# 4. FEATURES NGA DATA
# =====================================================

# Krijojmë numrin e ditëve nga data minimale
df["Date_Number"] = (
    df["Date"] -
    df["Date"].min()
).dt.days


# Marrim muajin
df["Month"] = df["Date"].dt.month


# Marrim ditën e javës
df["Day_of_Week"] = df["Date"].dt.dayofweek


# Shfaqim features
print(
    df[
        [
            "Date",
            "Date_Number",
            "Month",
            "Day_of_Week"
        ]
    ].head()
)


# =====================================================
# 5. NUMPY
# =====================================================

# E kthejmë Amount në NumPy array
amount_array = df["Amount"].to_numpy()


# Llogarisim mesataren
print("\n--- NumPy ---")
print("Amount Mean:", np.mean(amount_array))

# Gjejmë vlerën maksimale
print("Amount Max:", np.max(amount_array))

# Gjejmë vlerën minimale
print("Amount Min:", np.min(amount_array))

# Gjejmë shumën
print("Amount Total:", np.sum(amount_array))


# =====================================================
# 6. DATASET INFO
# =====================================================

# Shfaqim informacionin e dataset-it
print("\n--- Dataset Info ---")
df.info()


# =====================================================
# 7. MODEL AMOUNT
# =====================================================

# Features
X_amount = df[
    [
        "Date_Number",
        "Month",
        "Day_of_Week"
    ]
]


# Target
y_amount = df["Amount"]


# Ndajmë dataset-in në training dhe testing
x_train, x_test, y_train, y_test = train_test_split(
    X_amount,
    y_amount,
    random_state=42,
    test_size=0.2
)


# Krijojmë modelin
model_amount = LinearRegression()


# Trajnojmë modelin
model_amount.fit(
    x_train,
    y_train
)


# Bëjmë prediction
y_predict = model_amount.predict(
    x_test
)


# Llogarisim MAE
mae = mean_absolute_error(
    y_test,
    y_predict
)


# Llogarisim R2
rs = r2_score(
    y_test,
    y_predict
)


# Shfaqim rezultatet
print("\nAmount MAE:", mae)
print("Amount R2:", rs)


# =====================================================
# 8. MODEL EXPENSES
# =====================================================

X_expenses = df[
    [
        "Date_Number",
        "Month",
        "Day_of_Week"
    ]
]

y_expenses = df["Expenses"]


x_train, x_test, y_train, y_test = train_test_split(
    X_expenses,
    y_expenses,
    random_state=42,
    test_size=0.2
)


model_expenses = LinearRegression()


model_expenses.fit(
    x_train,
    y_train
)


y_predict = model_expenses.predict(
    x_test
)


mae = mean_absolute_error(
    y_test,
    y_predict
)


rs = r2_score(
    y_test,
    y_predict
)


print("\nExpenses MAE:", mae)
print("Expenses R2:", rs)


# =====================================================
# 9. MODEL REVENUE
# =====================================================

X_revenue = df[
    [
        "Date_Number",
        "Month",
        "Day_of_Week"
    ]
]

y_revenue = df["Revenue"]


x_train, x_test, y_train, y_test = train_test_split(
    X_revenue,
    y_revenue,
    random_state=42,
    test_size=0.2
)


model_revenue = LinearRegression()


model_revenue.fit(
    x_train,
    y_train
)


y_predict = model_revenue.predict(
    x_test
)


mae = mean_absolute_error(
    y_test,
    y_predict
)


rs = r2_score(
    y_test,
    y_predict
)


print("\nRevenue MAE:", mae)
print("Revenue R2:", rs)


# =====================================================
# 10. MODEL PROFIT
# =====================================================

X_profit = df[
    [
        "Date_Number",
        "Month",
        "Day_of_Week"
    ]
]

y_profit = df["Profit"]


x_train, x_test, y_train, y_test = train_test_split(
    X_profit,
    y_profit,
    random_state=42,
    test_size=0.2
)


model_profit = LinearRegression()


model_profit.fit(
    x_train,
    y_train
)


y_predict = model_profit.predict(
    x_test
)


mae = mean_absolute_error(
    y_test,
    y_predict
)


rs = r2_score(
    y_test,
    y_predict
)


print("\nProfit MAE:", mae)
print("Profit R2:", rs)


print("\nTë katër modelet u trajnuan me sukses!")


# =====================================================
# 11. MYSQL
# =====================================================

# Funksion për lidhjen me MySQL
def connect_mysql():
    required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing required database environment variable(s): " + ", ".join(missing))
    config = dict(
        host=os.getenv("DB_HOST").strip(),
        port=int(os.getenv("DB_PORT").strip()),
        user=os.getenv("DB_USER").strip(),
        password=os.getenv("DB_PASSWORD").strip(),
        database=os.getenv("DB_NAME").strip(),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
    )
    ssl_ca = os.getenv("DB_SSL_CA", "").strip()
    if ssl_ca:
        config["ssl_ca"] = ssl_ca
    return mysql.connector.connect(**config)


# =====================================================
# 12. MENUJA
# =====================================================

while True:

    print("\n======================================")
    print("           FINSIGHT AI")
    print("======================================")

    print("1 - Parashiko Amount")
    print("2 - Parashiko Expenses")
    print("3 - Parashiko Revenue")
    print("4 - Parashiko Profit")
    print("jo - Dil")

    print("======================================")


    # Marrim zgjedhjen
    choose = input("Zgjedhja: ")


    # =================================================
    # EXIT
    # =================================================

    if choose.lower() == "jo":

        print("Bye!")

        break


    # =================================================
    # AMOUNT
    # =================================================

    elif choose == "1":

        # Marrim datën
        date_input = input(
            "\nShkruaj datën (YYYY-MM-DD): "
        )


        # Provojmë datën
        try:

            date = pd.to_datetime(
                date_input
            )

        except:

            print(
                "Gabim! Përdor formatin YYYY-MM-DD."
            )

            continue


        # Krijojmë features për datën e re
        date_number = (
            date -
            df["Date"].min()
        ).days

        month = date.month

        day_of_week = date.dayofweek


        # Krijojmë DataFrame për prediction
        X_future = pd.DataFrame({
            "Date_Number": [date_number],
            "Month": [month],
            "Day_of_Week": [day_of_week]
        })


        # Bëjmë prediction
        prediction = model_amount.predict(
            X_future
        )


        # Marrim rezultatin
        predicted_amount = round(
            prediction[0],
            2
        )


        # Shfaqim prediction
        print(
            "\nAmount i parashikuar:",
            predicted_amount
        )


        # =================================================
        # GRAFIK I THJESHTË
        # =================================================

        # Krijojmë një grafik të vogël
        plt.figure(figsize=(6, 4))

        # Shfaqim Amount real sipas datës
        plt.plot(
            df["Date"],
            df["Amount"],
            marker="o"
        )

        # Shtojmë prediction-in në grafik
        plt.scatter(
            date,
            predicted_amount
        )

        # Titulli
        plt.title("Amount")

        # Emri i boshtit X
        plt.xlabel("Date")

        # Emri i boshtit Y
        plt.ylabel("Amount")

        # Shfaqim grafikun
        plt.tight_layout()

        plt.show()


        # =================================================
        # MYSQL
        # =================================================

        connect = connect_mysql()

        cursor = connect.cursor()


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS amount_prediction(
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                predicted_amount FLOAT
            )
        """)


        cursor.execute("""
            INSERT INTO amount_prediction
            (date, predicted_amount)
            VALUES (%s, %s)
        """, (
            date.strftime("%Y-%m-%d"),
            predicted_amount
        ))


        connect.commit()

        cursor.close()

        connect.close()


        print(
            "Amount prediction u ruajt në MySQL!"
        )


    # =================================================
    # EXPENSES
    # =================================================

    elif choose == "2":

        date_input = input(
            "\nShkruaj datën (YYYY-MM-DD): "
        )


        try:

            date = pd.to_datetime(
                date_input
            )

        except:

            print(
                "Gabim! Përdor formatin YYYY-MM-DD."
            )

            continue


        date_number = (
            date -
            df["Date"].min()
        ).days

        month = date.month

        day_of_week = date.dayofweek


        X_future = pd.DataFrame({
            "Date_Number": [date_number],
            "Month": [month],
            "Day_of_Week": [day_of_week]
        })


        prediction = model_expenses.predict(
            X_future
        )


        predicted_expenses = round(
            prediction[0],
            2
        )


        print(
            "\nExpenses i parashikuar:",
            predicted_expenses
        )


        # Grafik i thjeshtë
        plt.figure(figsize=(6, 4))

        plt.plot(
            df["Date"],
            df["Expenses"],
            marker="o"
        )

        plt.scatter(
            date,
            predicted_expenses
        )

        plt.title("Expenses")
        plt.xlabel("Date")
        plt.ylabel("Expenses")

        plt.tight_layout()

        plt.show()


        # MySQL
        connect = connect_mysql()

        cursor = connect.cursor()


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expenses_prediction(
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                predicted_expenses FLOAT
            )
        """)


        cursor.execute("""
            INSERT INTO expenses_prediction
            (date, predicted_expenses)
            VALUES (%s, %s)
        """, (
            date.strftime("%Y-%m-%d"),
            predicted_expenses
        ))


        connect.commit()

        cursor.close()

        connect.close()


        print(
            "Expenses prediction u ruajt në MySQL!"
        )


    # =================================================
    # REVENUE
    # =================================================

    elif choose == "3":

        date_input = input(
            "\nShkruaj datën (YYYY-MM-DD): "
        )


        try:

            date = pd.to_datetime(
                date_input
            )

        except:

            print(
                "Gabim! Përdor formatin YYYY-MM-DD."
            )

            continue


        date_number = (
            date -
            df["Date"].min()
        ).days

        month = date.month

        day_of_week = date.dayofweek


        X_future = pd.DataFrame({
            "Date_Number": [date_number],
            "Month": [month],
            "Day_of_Week": [day_of_week]
        })


        prediction = model_revenue.predict(
            X_future
        )


        predicted_revenue = round(
            prediction[0],
            2
        )


        print(
            "\nRevenue i parashikuar:",
            predicted_revenue
        )


        # Grafik i thjeshtë
        plt.figure(figsize=(6, 4))

        plt.plot(
            df["Date"],
            df["Revenue"],
            marker="o"
        )

        plt.scatter(
            date,
            predicted_revenue
        )

        plt.title("Revenue")
        plt.xlabel("Date")
        plt.ylabel("Revenue")

        plt.tight_layout()

        plt.show()


        # MySQL
        connect = connect_mysql()

        cursor = connect.cursor()


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS revenue_prediction(
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                predicted_revenue FLOAT
            )
        """)


        cursor.execute("""
            INSERT INTO revenue_prediction
            (date, predicted_revenue)
            VALUES (%s, %s)
        """, (
            date.strftime("%Y-%m-%d"),
            predicted_revenue
        ))


        connect.commit()

        cursor.close()

        connect.close()


        print(
            "Revenue prediction u ruajt në MySQL!"
        )


    # =================================================
    # PROFIT
    # =================================================

    elif choose == "4":

        date_input = input(
            "\nShkruaj datën (YYYY-MM-DD): "
        )


        try:

            date = pd.to_datetime(
                date_input
            )

        except:

            print(
                "Gabim! Përdor formatin YYYY-MM-DD."
            )

            continue


        date_number = (
            date -
            df["Date"].min()
        ).days

        month = date.month

        day_of_week = date.dayofweek


        X_future = pd.DataFrame({
            "Date_Number": [date_number],
            "Month": [month],
            "Day_of_Week": [day_of_week]
        })


        prediction = model_profit.predict(
            X_future
        )


        predicted_profit = round(
            prediction[0],
            2
        )


        print(
            "\nProfit i parashikuar:",
            predicted_profit
        )


        # Grafik i thjeshtë
        plt.figure(figsize=(6, 4))

        plt.plot(
            df["Date"],
            df["Profit"],
            marker="o"
        )

        plt.scatter(
            date,
            predicted_profit
        )

        plt.title("Profit")
        plt.xlabel("Date")
        plt.ylabel("Profit")

        plt.tight_layout()

        plt.show()


        # MySQL
        connect = connect_mysql()

        cursor = connect.cursor()


        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profit_prediction(
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                predicted_profit FLOAT
            )
        """)


        cursor.execute("""
            INSERT INTO profit_prediction
            (date, predicted_profit)
            VALUES (%s, %s)
        """, (
            date.strftime("%Y-%m-%d"),
            predicted_profit
        ))


        connect.commit()

        cursor.close()

        connect.close()


        print(
            "Profit prediction u ruajt në MySQL!"
        )


    # =================================================
    # GABIM
    # =================================================

    else:

        print(
            "\nGabim! Zgjidh 1, 2, 3, 4 ose jo."
        )
