import joblib
import pandas as pd
import sys
sys.path.insert(0, ".")
from src.model.train_baseline import prepare_features, train_test_split_grouped
from sksurv.linear_model import CoxPHSurvivalAnalysis
from src.utils.config import load_config
import os


cfg = load_config()
df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))
X, y, groups = prepare_features(df)
X_train, X_test, y_train, y_test, _, _ = train_test_split_grouped(X, y, groups)

model = CoxPHSurvivalAnalysis(alpha=1.0)
model.fit(X_train, y_train)

os.makedirs("models", exist_ok=True)   
joblib.dump({"model": model, "feature_names": list(X.columns)}, "models/cox_baseline_v1.joblib")

print("Saved to models/cox_baseline_v1.joblib")