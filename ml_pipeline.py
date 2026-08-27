"""
ML Pipeline Module - FinSight AI
================================
Comprehensive machine learning pipeline with proper validation,
no data leakage, cross-validation, model comparison, and feature engineering.

Key Features:
- Chronological train/test split for time-series data
- Time-series cross-validation (TimeSeriesSplit)
- Multiple model comparison (Linear, Ridge, Lasso, Tree, Forest, Boosting)
- Comprehensive evaluation metrics (MAE, RMSE, MSE, R², cross-val scores)
- Feature engineering (lag features, rolling averages, trend indicators)
- Proper preprocessing pipeline (fit on train only, apply to test/predict)
- Overfitting detection (train vs test comparison)
- Model persistence (pickle + metadata)
- Robust error handling and validation

Usage:
    from ml_pipeline import MLPipeline
    
    pipeline = MLPipeline()
    results = pipeline.train_all_models(df, user_id, company_id)
    prediction, confidence = pipeline.predict(user_id, model_type, features)
"""

from __future__ import annotations

import json
import logging
import pickle
import traceback
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit, cross_validate
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

MIN_TRAINING_SAMPLES = 10
MIN_UNIQUE_VALUES = 2
MAX_FEATURE_LAG = 30
RANDOM_SEED = 42

# Model configurations for comparison
REGRESSION_MODELS = {
    'linear': {
        'model': LinearRegression(),
        'name': 'Linear Regression',
        'params': {}
    },
    'ridge': {
        'model': Ridge(random_state=RANDOM_SEED),
        'name': 'Ridge Regression',
        'params': {
            'ridge__alpha': [0.001, 0.01, 0.1, 1.0, 10.0]
        }
    },
    'lasso': {
        'model': Lasso(random_state=RANDOM_SEED, max_iter=10000),
        'name': 'Lasso Regression',
        'params': {
            'lasso__alpha': [0.001, 0.01, 0.1, 1.0, 10.0]
        }
    },
    'tree': {
        'model': DecisionTreeRegressor(random_state=RANDOM_SEED, max_depth=10),
        'name': 'Decision Tree',
        'params': {
            'model__max_depth': [3, 5, 10, 15],
            'model__min_samples_leaf': [2, 5, 10]
        }
    },
    'forest': {
        'model': RandomForestRegressor(n_estimators=50, random_state=RANDOM_SEED, n_jobs=-1),
        'name': 'Random Forest',
        'params': {
            'model__max_depth': [10, 15, 20],
            'model__min_samples_leaf': [2, 5]
        }
    },
    'boosting': {
        'model': GradientBoostingRegressor(n_estimators=50, random_state=RANDOM_SEED),
        'name': 'Gradient Boosting',
        'params': {
            'model__max_depth': [3, 5, 7],
            'model__learning_rate': [0.01, 0.1]
        }
    }
}


class RuleBasedRiskModel:
    """Transparent risk scorer used instead of training on self-derived labels."""

    classes_ = np.array(["HIGH RISK", "MEDIUM RISK", "LOW RISK"])

    @staticmethod
    def _label(row: dict[str, Any]) -> str:
        def finite(name: str) -> float:
            try:
                value = float(row.get(name, 0) or 0)
                return value if np.isfinite(value) else 0.0
            except (TypeError, ValueError):
                return 0.0

        revenue = finite("revenue")
        expenses = finite("expenses")
        profit = finite("profit")
        if profit < 0 or expenses > revenue:
            return "HIGH RISK"
        if revenue > 0 and profit / revenue < 0.08:
            return "MEDIUM RISK"
        return "LOW RISK"

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.array([self._label(row) for row in frame.to_dict(orient="records")])

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        labels = self.predict(frame)
        return np.array([[1.0 if label == class_name else 0.0 for class_name in self.classes_]
                         for label in labels])

# ============================================================================
# Data Validation & Preprocessing
# ============================================================================

class DataValidator:
    """Comprehensive data validation for ML pipeline."""
    
    @staticmethod
    def validate_dataframe(df: pd.DataFrame, columns_required: list = None) -> Tuple[bool, str]:
        """Validate DataFrame structure and content."""
        if df is None or df.empty:
            return False, "DataFrame is empty or None"
        
        if columns_required:
            missing = [c for c in columns_required if c not in df.columns]
            if missing:
                return False, f"Missing required columns: {missing}"
        
        # Optional upload columns (for example tx_type/category) may be blank
        # and must never prevent a financial model from training.  Only an
        # explicitly required feature/target is a validation failure.
        checked_columns = columns_required or []
        all_nan_cols = [c for c in checked_columns if df[c].isna().all()]
        if all_nan_cols:
            return False, f"Columns are entirely NaN: {all_nan_cols}"
        
        return True, "OK"
    
    @staticmethod
    def check_data_quality(df: pd.DataFrame, target: str) -> Dict[str, Any]:
        """Detailed data quality report."""
        report = {
            'total_rows': len(df),
            'total_columns': len(df.columns),
            'duplicates': df.duplicated().sum(),
            'missing_values': df.isna().sum().to_dict(),
            'target_missing': df[target].isna().sum() if target in df.columns else 'N/A',
            'target_unique': df[target].nunique() if target in df.columns else 'N/A',
            'date_range': None,
            'date_span_days': None,
            'numeric_stats': {},
        }
        
        # Date range if available
        if 'tx_date' in df.columns:
            valid_dates = pd.to_datetime(df['tx_date'], errors='coerce').dropna()
            if not valid_dates.empty:
                min_date = valid_dates.min()
                max_date = valid_dates.max()
                report['date_range'] = f"{min_date.date()} to {max_date.date()}"
                report['date_span_days'] = (max_date - min_date).days
        
        # Numeric statistics
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            valid_vals = df[col].dropna()
            if len(valid_vals) > 0:
                report['numeric_stats'][col] = {
                    'mean': float(valid_vals.mean()),
                    'std': float(valid_vals.std()),
                    'min': float(valid_vals.min()),
                    'max': float(valid_vals.max()),
                }
        
        return report

# ============================================================================
# Feature Engineering
# ============================================================================

class FeatureEngineer:
    """Create and engineer features for ML models."""
    
    @staticmethod
    def create_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
        """Create temporal features from tx_date."""
        result = df.copy()
        
        if 'tx_date' not in result.columns:
            return result
        
        try:
            result['tx_date'] = pd.to_datetime(result['tx_date'], errors='coerce')
            
            # Date-based features (no future information leak)
            result['day_of_month'] = result['tx_date'].dt.day
            result['month'] = result['tx_date'].dt.month
            result['quarter'] = result['tx_date'].dt.quarter
            result['day_of_week'] = result['tx_date'].dt.dayofweek
            result['is_month_end'] = result['tx_date'].dt.is_month_end.astype(int)
            result['is_quarter_end'] = result['tx_date'].dt.is_quarter_end.astype(int)
            result['days_since_start'] = (result['tx_date'] - result['tx_date'].min()).dt.days
            
        except Exception as e:
            logger.warning(f"Failed to create temporal features: {e}")
        
        return result
    
    @staticmethod
    def create_lag_features(df: pd.DataFrame, lags: list = None, columns: list = None) -> pd.DataFrame:
        """Create lag features (previous values)."""
        if lags is None:
            lags = [1, 7, 30]
        if columns is None:
            columns = ['amount', 'revenue', 'expenses', 'profit']
        
        result = df.copy()
        
        if 'tx_date' not in result.columns:
            return result
        
        try:
            # Sort by date to ensure proper lag ordering
            result = result.sort_values('tx_date').reset_index(drop=True)
            
            for col in columns:
                if col not in result.columns:
                    continue
                
                for lag in lags:
                    result[f'{col}_lag{lag}'] = result[col].shift(lag)
        
        except Exception as e:
            logger.warning(f"Failed to create lag features: {e}")
        
        return result
    
    @staticmethod
    def create_rolling_features(df: pd.DataFrame, windows: list = None, columns: list = None) -> pd.DataFrame:
        """Create rolling average/sum features."""
        if windows is None:
            windows = [7, 30]
        if columns is None:
            columns = ['amount', 'revenue', 'expenses', 'profit']
        
        result = df.copy()
        
        if 'tx_date' not in result.columns:
            return result
        
        try:
            # Sort by date
            result = result.sort_values('tx_date').reset_index(drop=True)
            
            for col in columns:
                if col not in result.columns:
                    continue
                
                for window in windows:
                    # Shift first so a row never uses its own target value.
                    history = result[col].shift(1)
                    result[f'{col}_rolling_mean_{window}'] = history.rolling(window=window, min_periods=1).mean()
                    result[f'{col}_rolling_sum_{window}'] = history.rolling(window=window, min_periods=1).sum()
        
        except Exception as e:
            logger.warning(f"Failed to create rolling features: {e}")
        
        return result
    
    @staticmethod
    def create_derived_features(df: pd.DataFrame) -> pd.DataFrame:
        """Create business logic features."""
        result = df.copy()
        
        try:
            # Profit margin
            if 'revenue' in result.columns and 'profit' in result.columns:
                result['profit_margin'] = (result['profit'] / result['revenue'].replace(0, np.nan)).fillna(0)
            
            # Expense ratio
            if 'expenses' in result.columns and 'revenue' in result.columns:
                result['expense_ratio'] = (result['expenses'] / result['revenue'].replace(0, np.nan)).fillna(0)
            
            # Revenue growth (change from previous value)
            if 'revenue' in result.columns:
                result['revenue_change'] = result['revenue'].diff().fillna(0)
            
            # Expense growth
            if 'expenses' in result.columns:
                result['expense_change'] = result['expenses'].diff().fillna(0)
        
        except Exception as e:
            logger.warning(f"Failed to create derived features: {e}")
        
        return result

# ============================================================================
# ML Pipeline
# ============================================================================

class MLPipeline:
    """Complete ML pipeline with proper validation and no data leakage."""
    
    def __init__(self, models_dir: str = "models"):
        """Initialize pipeline."""
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(exist_ok=True)
        self.validator = DataValidator()
        self.engineer = FeatureEngineer()
    
    def _get_model_path(self, user_id: int, model_type: str) -> Path:
        """Get file path for persisted model."""
        return self.models_dir / f"user_{user_id}_{model_type}_model.pkl"
    
    def _get_metadata_path(self, user_id: int, model_type: str) -> Path:
        """Get file path for model metadata."""
        return self.models_dir / f"user_{user_id}_{model_type}_meta.json"
    
    def _save_model(self, user_id: int, model_type: str, model: Any, metadata: Dict) -> bool:
        """Save trained model and metadata to disk."""
        try:
            model_path = self._get_model_path(user_id, model_type)
            meta_path = self._get_metadata_path(user_id, model_type)
            
            with open(model_path, 'wb') as f:
                pickle.dump(model, f)
            
            with open(meta_path, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            
            logger.info(f"Model saved: {user_id}/{model_type}")
            return True
        except Exception as e:
            logger.error(f"Failed to save model {user_id}/{model_type}: {e}")
            return False
    
    def _load_model(self, user_id: int, model_type: str) -> Tuple[Optional[Any], Optional[Dict]]:
        """Load trained model and metadata from disk."""
        try:
            model_path = self._get_model_path(user_id, model_type)
            meta_path = self._get_metadata_path(user_id, model_type)
            
            if not model_path.exists() or not meta_path.exists():
                return None, None
            
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            
            with open(meta_path, 'r') as f:
                metadata = json.load(f)
            
            logger.info(f"Model loaded: {user_id}/{model_type}")
            return model, metadata
        except Exception as e:
            logger.error(f"Failed to load model {user_id}/{model_type}: {e}")
            return None, None
    
    def _prepare_training_data(self, df: pd.DataFrame, target: str) -> Tuple[Optional[pd.DataFrame], str]:
        """Prepare and validate training data."""
        is_valid, msg = self.validator.validate_dataframe(df, ['tx_date', target])
        if not is_valid:
            return None, msg
        
        working = df.copy()
        working['tx_date'] = pd.to_datetime(working['tx_date'], errors='coerce')
        working[target] = pd.to_numeric(working[target], errors='coerce').replace([np.inf, -np.inf], np.nan)
        
        # Remove rows with missing target or date
        working = working.dropna(subset=['tx_date', target])
        
        if len(working) < MIN_TRAINING_SAMPLES:
            return None, f"Insufficient training samples: {len(working)} < {MIN_TRAINING_SAMPLES}"
        
        # Check target has enough variation
        if working[target].nunique() < MIN_UNIQUE_VALUES:
            return None, f"Target '{target}' has insufficient variation (< {MIN_UNIQUE_VALUES} unique values)"
        
        # Sort by date to maintain temporal order
        working = working.sort_values('tx_date').reset_index(drop=True)
        
        # Create features
        working = self.engineer.create_temporal_features(working)
        working = self.engineer.create_lag_features(working, lags=[1, 7, 30])
        working = self.engineer.create_rolling_features(working, windows=[7, 30])
        working = self.engineer.create_derived_features(working)
        
        return working, "OK"
    
    def _select_features(self, df: pd.DataFrame, target: str) -> list:
        """Select features that don't have target leakage."""
        # Only retain values available at forecast time.  Raw same-period
        # financial columns and derived ratios would leak future information.
        temporal = {"day_of_month", "month", "quarter", "day_of_week",
                    "is_month_end", "is_quarter_end", "days_since_start"}
        numeric_cols = [c for c in df.select_dtypes(include=[np.number]).columns
                        if c in temporal or c.startswith(f"{target}_lag")
                        or c.startswith(f"{target}_rolling_")]

        # The first fold of the time-series cross-validation has only about a
        # quarter of the training rows.  A lag longer than that fold is empty
        # there and only creates noisy imputer warnings, so use it only when
        # the dataset is large enough to support it.
        minimum_cv_history = max(1, len(df) // 4 - 1)
        numeric_cols = [
            column for column in numeric_cols
            if not (column.startswith(f"{target}_lag") and
                    column.rsplit("lag", 1)[-1].isdigit() and
                    int(column.rsplit("lag", 1)[-1]) > minimum_cv_history)
        ]
        
        # Remove columns with too many NaN
        valid_cols = []
        for col in numeric_cols:
            if df[col].isna().sum() / len(df) < 0.8:  # Max 80% missing
                valid_cols.append(col)
        
        return sorted(valid_cols)
    
    def _split_training_data(self, df: pd.DataFrame, test_size: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Split data chronologically (no random mixing)."""
        split_idx = int(len(df) * (1 - test_size))
        train_df = df.iloc[:split_idx].copy()
        test_df = df.iloc[split_idx:].copy()
        return train_df, test_df
    
    def _build_pipeline(self, model_config: Dict, scaler: bool = True) -> Pipeline:
        """Build preprocessing + model pipeline."""
        steps = [('imputer', SimpleImputer(strategy='median'))]
        
        if scaler:
            steps.append(('scaler', StandardScaler()))
        
        steps.append(('model', model_config['model']))
        
        return Pipeline(steps)
    
    def _evaluate_model(self, model: Any, X_train: pd.DataFrame, y_train: pd.Series,
                       X_test: pd.DataFrame, y_test: pd.Series, features: list) -> Dict:
        """Comprehensive model evaluation."""
        metrics = {
            'n_train': len(X_train),
            'n_test': len(X_test),
            'features_used': features,
            'feature_count': len(features),
        }
        
        try:
            # Training predictions
            y_train_pred = model.predict(X_train)
            metrics['train_mae'] = float(mean_absolute_error(y_train, y_train_pred))
            metrics['train_mse'] = float(mean_squared_error(y_train, y_train_pred))
            metrics['train_rmse'] = float(np.sqrt(metrics['train_mse']))
            metrics['train_r2'] = float(r2_score(y_train, y_train_pred))
            
            # Test predictions
            y_test_pred = model.predict(X_test)
            metrics['test_mae'] = float(mean_absolute_error(y_test, y_test_pred))
            metrics['test_mse'] = float(mean_squared_error(y_test, y_test_pred))
            metrics['test_rmse'] = float(np.sqrt(metrics['test_mse']))
            metrics['test_r2'] = float(r2_score(y_test, y_test_pred))
            
            # Overfitting detection
            train_test_gap = abs(metrics['train_r2'] - metrics['test_r2'])
            metrics['overfitting_gap'] = float(train_test_gap)
            if train_test_gap > 0.15:
                metrics['overfitting_warning'] = "High (R² gap > 0.15)"
            elif train_test_gap > 0.10:
                metrics['overfitting_warning'] = "Moderate (R² gap > 0.10)"
            else:
                metrics['overfitting_warning'] = "Low"
            
            # Cross-validation (TimeSeriesSplit)
            tscv = TimeSeriesSplit(n_splits=3)
            cv_scores = cross_validate(
                model, X_train, y_train, cv=tscv,
                scoring=['r2', 'neg_mean_absolute_error'],
                return_train_score=True
            )
            
            metrics['cv_r2_mean'] = float(cv_scores['test_r2'].mean())
            metrics['cv_r2_std'] = float(cv_scores['test_r2'].std())
            metrics['cv_mae_mean'] = float(-cv_scores['test_neg_mean_absolute_error'].mean())
            metrics['cv_mae_std'] = float(cv_scores['test_neg_mean_absolute_error'].std())
            
        except Exception as e:
            logger.warning(f"Evaluation error: {e}")
            metrics['evaluation_error'] = str(e)
        
        return metrics
    
    def train_regression_model(self, df: pd.DataFrame, target: str, user_id: int,
                              model_type: str = 'best') -> Dict:
        """Train regression model with proper validation."""
        result = {
            'success': False,
            'model_type': target,
            'user_id': user_id,
            'timestamp': datetime.now().isoformat(),
            'error': None,
            'metrics': {},
            'selected_model': None,
        }
        
        try:
            # Prepare data
            working, msg = self._prepare_training_data(df, target)
            if working is None:
                result['error'] = msg
                logger.warning(f"Data preparation failed for {target}: {msg}")
                return result
            
            # Select features
            features = self._select_features(working, target)
            if not features:
                result['error'] = "No valid features found after preprocessing"
                return result
            
            # Split chronologically
            train_df, test_df = self._split_training_data(working)
            if len(train_df) < MIN_TRAINING_SAMPLES or len(test_df) < 2:
                result['error'] = f"Insufficient data for train/test split"
                return result

            # A lag can be valid in the full frame but still be entirely empty
            # in a short training partition. Drop those columns before fitting
            # so sklearn does not silently skip them during imputation.
            features = [feature for feature in features if train_df[feature].notna().any()]
            if not features:
                result['error'] = "No valid features found in the training period"
                return result
            
            X_train = train_df[features].apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
            y_train = pd.to_numeric(train_df[target], errors='coerce').replace([np.inf, -np.inf], np.nan)
            X_test = test_df[features].apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
            y_test = pd.to_numeric(test_df[target], errors='coerce').replace([np.inf, -np.inf], np.nan)
            
            # Train and compare models
            best_model = None
            best_r2 = -np.inf
            best_model_name = None
            all_models = {}
            
            for model_key, config in REGRESSION_MODELS.items():
                try:
                    pipeline = self._build_pipeline(config)
                    pipeline.fit(X_train, y_train)
                    
                    metrics = self._evaluate_model(pipeline, X_train, y_train, X_test, y_test, features)
                    metrics['model_name'] = config['name']
                    all_models[model_key] = metrics
                    
                    # Select best based on test R²
                    test_r2 = metrics.get('test_r2', -np.inf)
                    if test_r2 > best_r2:
                        best_r2 = test_r2
                        best_model = pipeline
                        best_model_name = model_key
                    
                    logger.info(f"Model {model_key}: R²={test_r2:.4f}")
                
                except Exception as e:
                    logger.warning(f"Failed to train {model_key}: {e}")
                    all_models[model_key] = {'error': str(e)}
            
            if best_model is None:
                result['error'] = "No models trained successfully"
                return result
            
            # Save best model
            metadata = {
                'target': target,
                'features': features,
                'best_model': best_model_name,
                'all_models': all_models,
                'training_date': datetime.now().isoformat(),
                'data_quality': self.validator.check_data_quality(df, target),
            }
            
            self._save_model(user_id, target, best_model, metadata)
            
            result['success'] = True
            result['selected_model'] = best_model_name
            result['metrics'] = all_models.get(best_model_name, {})
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Regression training error: {e}\n{traceback.format_exc()}")
        
        return result
    
    def train_risk_classifier(self, df: pd.DataFrame, user_id: int) -> Dict:
        """Build a transparent risk model from business rules.

        Risk labels are defined by the same current-period financial values
        supplied to the model. Training a classifier on those values would
        leak the target into the features, so the public API persists a
        transparent rule model instead.
        """
        result = {
            'success': False,
            'user_id': user_id,
            'timestamp': datetime.now().isoformat(),
            'error': None,
            'metrics': {},
        }

        try:
            is_valid, msg = self.validator.validate_dataframe(df, ['tx_date'])
            if not is_valid:
                result['error'] = msg
                return result
            working = df.copy()
            working['tx_date'] = pd.to_datetime(working['tx_date'], errors='coerce')
            working = working.dropna(subset=['tx_date'])
            features = ['revenue', 'expenses', 'profit', 'amount']
            for feature in features:
                if feature not in working:
                    working[feature] = np.nan
                working[feature] = pd.to_numeric(working[feature], errors='coerce').replace([np.inf, -np.inf], np.nan)
            daily_data = working.groupby(working['tx_date'].dt.date)[features].sum(min_count=1)
            daily_data = daily_data.replace([np.inf, -np.inf], np.nan).fillna(
                daily_data.median(numeric_only=True)).fillna(0)
            if len(daily_data) < MIN_TRAINING_SAMPLES:
                result['error'] = f"Insufficient daily periods: {len(daily_data)} < {MIN_TRAINING_SAMPLES}"
                return result

            rule_model = RuleBasedRiskModel()
            labels = rule_model.predict(daily_data[features])
            split_idx = max(1, int(len(daily_data) * 0.8))
            train_labels, test_labels = labels[:split_idx], labels[split_idx:]
            metrics = {
                'features_used': features,
                'n_daily_periods': len(daily_data),
                'n_train': len(train_labels),
                'n_test': len(test_labels),
                'train_accuracy': 1.0,
                'test_accuracy': 1.0 if len(test_labels) else None,
                'train_f1_weighted': 1.0,
                'test_f1_weighted': 1.0 if len(test_labels) else None,
                'evaluation_method': 'deterministic_business_rules',
                'overfitting_warning': 'Not applicable: no learned classifier is used.',
            }
            metadata = {
                'features': features,
                'rule_based': True,
                'label_distribution': pd.Series(labels).value_counts().to_dict(),
                'training_date': datetime.now().isoformat(),
                'risk_thresholds': {
                    'high_risk': 'profit < 0 OR expenses > revenue',
                    'medium_risk': 'revenue > 0 AND profit_margin < 0.08',
                    'low_risk': 'otherwise',
                },
            }
            self._save_model(user_id, 'risk', rule_model, metadata)
            result['success'] = True
            result['metrics'] = metrics
            return result
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Rule-based risk training error: {e}\\n{traceback.format_exc()}")
            return result
        
    
    def _forecast_features(self, history_df: pd.DataFrame, target: str,
                           prediction_date: date, features: list) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """Recreate the training feature engineering for one future row."""
        if history_df is None or history_df.empty or "tx_date" not in history_df or target not in history_df:
            return None, "The active dataset has no dated history for this prediction."
        history = history_df.copy()
        history["tx_date"] = pd.to_datetime(history["tx_date"], errors="coerce")
        history[target] = pd.to_numeric(history[target], errors="coerce")
        target_ts = pd.Timestamp(prediction_date)
        history = history.dropna(subset=["tx_date", target])
        # A forecast may use only observations before the requested day.
        history = history[history["tx_date"] < target_ts].sort_values("tx_date").reset_index(drop=True)
        if history.empty:
            return None, "No historical observations exist before the requested prediction date."
        future = pd.concat([history, pd.DataFrame([{"tx_date": target_ts, target: np.nan}])], ignore_index=True)
        future = self.engineer.create_temporal_features(future)
        future = self.engineer.create_lag_features(future, lags=[1, 7, 30], columns=[target])
        future = self.engineer.create_rolling_features(future, windows=[7, 30], columns=[target])
        missing = [name for name in features if name not in future.columns]
        if missing:
            return None, f"Saved model requires unavailable features: {missing}"
        X = future.iloc[[-1]][features].apply(pd.to_numeric, errors="coerce")
        return X, None

    def predict_regression(self, user_id: int, model_type: str,
                          new_data: Dict, history_df: pd.DataFrame = None,
                          prediction_date: date = None) -> Tuple[Optional[float], Optional[Dict]]:
        """Make regression prediction."""
        try:
            model, metadata = self._load_model(user_id, model_type)
            
            if model is None or metadata is None:
                logger.error(f"Model not found: {user_id}/{model_type}")
                return None, {'error': f"Model not trained for {model_type}"}
            
            features = metadata.get('features', [])
            
            if history_df is not None and prediction_date is not None:
                X, error = self._forecast_features(history_df, model_type, prediction_date, features)
                if error:
                    return None, {'error': error}
            else:
                input_df = pd.DataFrame([new_data or {}]).reindex(columns=features)
                if not any(feature in (new_data or {}) for feature in features):
                    return None, {'error': "Prediction input does not contain any recognized model features."}
                missing = [feature for feature in features if feature not in (new_data or {})]
                X = input_df.apply(pd.to_numeric, errors="coerce")
                X = X.replace([np.inf, -np.inf], np.nan)
            
            # Predict
            prediction = float(model.predict(X)[0])
            
            # Estimate uncertainty from test metrics
            metrics = metadata.get('all_models', {}).get(metadata.get('best_model', {}), {})
            test_rmse = metrics.get('test_rmse', 0)
            
            return prediction, {
                'success': True,
                'prediction': prediction,
                'model_type': model_type,
                'features_used': len(features),
                'estimated_error': test_rmse,
                'model_name': metadata.get('best_model'),
                'imputed_features': missing,
            }
        
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return None, {'error': f"Prediction failed: {e}"}
    
    def predict_risk(self, user_id: int, features_dict: Dict) -> Tuple[Optional[str], Optional[Dict]]:
        """Classify risk for given features."""
        try:
            model, metadata = self._load_model(user_id, 'risk')
            
            if model is None or metadata is None:
                return None, {'error': "Risk model not trained"}
            
            features = metadata.get('features', [])
            
            # Prepare input
            input_data = []
            for feature in features:
                input_data.append(features_dict.get(feature, 0))
            
            X = pd.DataFrame([input_data], columns=features).apply(pd.to_numeric, errors='coerce')
            if not np.isfinite(X.to_numpy()).all():
                return None, {'error': "Risk prediction input contains missing or non-finite values."}
            
            # Predict
            risk_label = str(model.predict(X)[0])
            
            # Get probabilities if available
            try:
                proba = model.predict_proba(X)[0]
                class_proba = dict(zip(model.classes_, proba))
            except:
                class_proba = {}
            
            return risk_label, {
                'success': True,
                'risk_level': risk_label,
                'probabilities': class_proba,
            }
        
        except Exception as e:
            logger.error(f"Risk prediction error: {e}")
            return None, {'error': f"Risk prediction failed: {e}"}

# ============================================================================
# Convenience functions for backward compatibility
# ============================================================================

_pipeline_instance = None

def get_pipeline() -> MLPipeline:
    """Get or create global pipeline instance."""
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = MLPipeline()
    return _pipeline_instance

def train_all_models(df: pd.DataFrame, user_id: int, company_id: int = None) -> Dict:
    """Train all models for a user. Backward compatible with app.py."""
    pipeline = get_pipeline()
    results = {
        'user_id': user_id,
        'company_id': company_id,
        'timestamp': datetime.now().isoformat(),
        'regression_models': {},
        'risk_model': None,
    }
    
    # Train regression models for each target
    for target in ['amount', 'revenue', 'expenses', 'profit']:
        result = pipeline.train_regression_model(df, target, user_id)
        results['regression_models'][target] = result
    
    # Train risk model
    results['risk_model'] = pipeline.train_risk_classifier(df, user_id)
    
    return results

if __name__ == '__main__':
    # Example usage
    print("ML Pipeline Module Loaded")
    print(f"Regression models available: {list(REGRESSION_MODELS.keys())}")
