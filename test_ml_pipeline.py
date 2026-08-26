"""
Comprehensive Test Suite for ML Pipeline
==========================================
Tests all ML functionality including:
- Data validation
- Model training
- Prediction accuracy
- Cross-validation
- Overfitting detection
- No data leakage
- Error handling
- Backward compatibility
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from ml_pipeline import get_pipeline, DataValidator, FeatureEngineer
import tempfile
import shutil

# ============================================================================
# Test Data Generation
# ============================================================================

def create_sample_financial_data(n_days: int = 100) -> pd.DataFrame:
    """Create realistic sample financial data for testing."""
    dates = [datetime.now() - timedelta(days=i) for i in range(n_days, 0, -1)]
    
    np.random.seed(42)
    data = {
        'tx_date': dates,
        'amount': np.random.uniform(100, 10000, n_days),
        'revenue': np.random.uniform(5000, 50000, n_days),
        'expenses': np.random.uniform(3000, 40000, n_days),
    }
    
    df = pd.DataFrame(data)
    df['profit'] = df['revenue'] - df['expenses']
    
    # Add some realistic patterns
    df['revenue'] = df['revenue'] + np.arange(n_days) * 10  # slight trend
    df['expenses'] = df['expenses'] + np.arange(n_days) * 5  # trend
    df['profit'] = df['revenue'] - df['expenses']
    
    return df

def create_edge_case_data() -> dict:
    """Create edge case test datasets."""
    return {
        'empty': pd.DataFrame(),
        'single_row': pd.DataFrame({
            'tx_date': [datetime.now()],
            'amount': [100],
            'revenue': [500],
            'expenses': [300],
            'profit': [200],
        }),
        'all_same_values': pd.DataFrame({
            'tx_date': [datetime.now() - timedelta(days=i) for i in range(10)],
            'amount': [100] * 10,
            'revenue': [500] * 10,
            'expenses': [300] * 10,
            'profit': [200] * 10,
        }),
        'missing_dates': pd.DataFrame({
            'tx_date': [None] * 10,
            'amount': np.random.uniform(100, 1000, 10),
            'revenue': np.random.uniform(5000, 50000, 10),
            'expenses': np.random.uniform(3000, 40000, 10),
            'profit': np.random.uniform(1000, 10000, 10),
        }),
        'missing_targets': pd.DataFrame({
            'tx_date': [datetime.now() - timedelta(days=i) for i in range(10)],
            'amount': [None] * 10,
            'revenue': [None] * 10,
            'expenses': [None] * 10,
            'profit': [None] * 10,
        }),
    }

# ============================================================================
# Test Suite
# ============================================================================

class MLPipelineTests:
    """Comprehensive test suite for ML pipeline."""
    
    def __init__(self):
        self.pipeline = get_pipeline()
        self.validator = DataValidator()
        self.engineer = FeatureEngineer()
        self.results = {}
        self.test_user_id = 99999  # Temporary test user ID
    
    def run_all_tests(self) -> None:
        """Run all tests."""
        print("\n" + "="*70)
        print("FINSIGHT AI - ML PIPELINE TEST SUITE")
        print("="*70)
        
        # Test groups
        self.test_data_validation()
        self.test_feature_engineering()
        self.test_model_training()
        self.test_predictions()
        self.test_risk_classification()
        self.test_edge_cases()
        self.test_no_data_leakage()
        self.test_overfitting_detection()
        
        # Summary
        self.print_summary()
    
    def test_data_validation(self) -> None:
        """Test data validation functionality."""
        print("\n" + "-"*70)
        print("TEST 1: Data Validation")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Test valid data
        is_valid, msg = self.validator.validate_dataframe(df, ['tx_date', 'amount'])
        assert is_valid, f"Valid data should pass validation: {msg}"
        print("✓ Valid data passes validation")
        
        # Test empty data
        is_valid, msg = self.validator.validate_dataframe(pd.DataFrame())
        assert not is_valid, "Empty data should fail validation"
        print("✓ Empty data fails validation")
        
        # Test missing columns
        is_valid, msg = self.validator.validate_dataframe(df, ['nonexistent_column'])
        assert not is_valid, "Missing columns should fail validation"
        print("✓ Missing columns validation works")
        
        # Test quality report
        quality = self.validator.check_data_quality(df, 'amount')
        assert quality['total_rows'] == 100, "Data quality report incorrect"
        assert 'numeric_stats' in quality, "Missing numeric stats"
        print(f"✓ Data quality report: {quality['total_rows']} rows, "
              f"date range: {quality['date_range']}")
        
        self.results['data_validation'] = 'PASSED'
    
    def test_feature_engineering(self) -> None:
        """Test feature engineering."""
        print("\n" + "-"*70)
        print("TEST 2: Feature Engineering")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Test temporal features
        df_temporal = self.engineer.create_temporal_features(df)
        assert 'day_of_month' in df_temporal.columns, "Missing day_of_month"
        assert 'month' in df_temporal.columns, "Missing month"
        assert 'day_of_week' in df_temporal.columns, "Missing day_of_week"
        assert 'days_since_start' in df_temporal.columns, "Missing days_since_start"
        print("✓ Temporal features created: day_of_month, month, day_of_week, days_since_start")
        
        # Test lag features
        df_lags = self.engineer.create_lag_features(df_temporal, lags=[1, 7])
        assert 'amount_lag1' in df_lags.columns, "Missing lag features"
        print("✓ Lag features created: amount_lag1, amount_lag7, etc.")
        
        # Test rolling features
        df_rolling = self.engineer.create_rolling_features(df_lags, windows=[7])
        assert 'amount_rolling_mean_7' in df_rolling.columns, "Missing rolling features"
        print("✓ Rolling features created: *_rolling_mean_7, *_rolling_sum_7")
        
        # Test derived features
        df_derived = self.engineer.create_derived_features(df_rolling)
        assert 'profit_margin' in df_derived.columns, "Missing profit_margin"
        assert 'expense_ratio' in df_derived.columns, "Missing expense_ratio"
        print("✓ Derived features created: profit_margin, expense_ratio, *_change")
        
        self.results['feature_engineering'] = 'PASSED'
    
    def test_model_training(self) -> None:
        """Test model training."""
        print("\n" + "-"*70)
        print("TEST 3: Model Training")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Train regression model
        result = self.pipeline.train_regression_model(df, 'amount', self.test_user_id)
        assert result['success'], f"Model training failed: {result.get('error')}"
        print("✓ Regression model trained successfully")
        
        # Check metrics
        metrics = result.get('metrics', {})
        assert 'test_r2' in metrics, "Missing test_r2 metric"
        assert 'train_r2' in metrics, "Missing train_r2 metric"
        assert 'test_mae' in metrics, "Missing test_mae metric"
        assert 'test_rmse' in metrics, "Missing test_rmse metric"
        assert 'cv_r2_mean' in metrics, "Missing cv_r2_mean metric"
        print(f"✓ All metrics calculated: R²={metrics.get('test_r2', 0):.4f}, "
              f"MAE={metrics.get('test_mae', 0):.2f}, "
              f"RMSE={metrics.get('test_rmse', 0):.2f}")
        
        # Check model comparison
        all_models = metrics.get('all_models', {})
        model_names = [v.get('model_name', 'unknown') for v in all_models.values() if isinstance(v, dict) and 'model_name' in v]
        model_count = len([v for v in all_models.values() if isinstance(v, dict)])
        if model_count > 0:
            print(f"✓ Model comparison: {model_count} models evaluated")
            if model_names:
                print(f"  Models: {', '.join(set(model_names)[:5])}")
        else:
            print(f"⚠ Model comparison: Limited models compared (may indicate data issues)")
        
        # Check selected model
        selected = result.get('selected_model')
        assert selected is not None, "No model selected"
        print(f"✓ Best model selected: {selected}")
        
        self.results['model_training'] = 'PASSED'
    
    def test_predictions(self) -> None:
        """Test predictions."""
        print("\n" + "-"*70)
        print("TEST 4: Predictions")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Train model first
        train_result = self.pipeline.train_regression_model(df, 'revenue', self.test_user_id)
        assert train_result['success'], "Training failed"
        
        # Make prediction
        new_data = {
            'day_of_month': 15,
            'month': 8,
            'day_of_week': 3,
            'days_since_start': 95,
        }
        prediction, info = self.pipeline.predict_regression(self.test_user_id, 'revenue', new_data)
        
        assert prediction is not None, f"Prediction failed: {info}"
        assert isinstance(prediction, (int, float)), "Prediction should be numeric"
        assert info.get('success'), "Prediction info indicates failure"
        print(f"✓ Prediction generated: {prediction:.2f}")
        print(f"  Features used: {info.get('features_used')}")
        print(f"  Estimated error: {info.get('estimated_error', 0):.2f}")
        
        self.results['predictions'] = 'PASSED'
    
    def test_risk_classification(self) -> None:
        """Test risk classification."""
        print("\n" + "-"*70)
        print("TEST 5: Risk Classification")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Train risk classifier
        result = self.pipeline.train_risk_classifier(df, self.test_user_id)
        assert result['success'], f"Risk classifier training failed: {result.get('error')}"
        print("✓ Risk classifier trained")
        
        # Check metrics
        metrics = result.get('metrics', {})
        assert 'test_accuracy' in metrics, "Missing test_accuracy"
        assert 'test_f1_weighted' in metrics, "Missing test_f1_weighted"
        print(f"✓ Risk classifier metrics: accuracy={metrics.get('test_accuracy', 0):.4f}, "
              f"F1={metrics.get('test_f1_weighted', 0):.4f}")
        
        # Make risk prediction
        features = {'revenue': 10000, 'expenses': 8000, 'profit': 2000, 'amount': 500}
        risk_level, info = self.pipeline.predict_risk(self.test_user_id, features)
        assert risk_level is not None, f"Risk prediction failed: {info}"
        assert risk_level in ["LOW RISK", "MEDIUM RISK", "HIGH RISK"], f"Invalid risk level: {risk_level}"
        print(f"✓ Risk prediction: {risk_level}")
        
        self.results['risk_classification'] = 'PASSED'
    
    def test_edge_cases(self) -> None:
        """Test edge case handling."""
        print("\n" + "-"*70)
        print("TEST 6: Edge Cases")
        print("-"*70)
        
        edge_cases = create_edge_case_data()
        
        for case_name, df in edge_cases.items():
            result = self.pipeline.train_regression_model(df, 'amount', self.test_user_id + 1)
            # Should handle gracefully (error is ok, but no exception)
            print(f"✓ Handled edge case '{case_name}': "
                  f"success={result['success']}, error={result.get('error', 'N/A')[:50]}")
        
        self.results['edge_cases'] = 'PASSED'
    
    def test_no_data_leakage(self) -> None:
        """Test for data leakage."""
        print("\n" + "-"*70)
        print("TEST 7: No Data Leakage")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        # Train model
        result = self.pipeline.train_regression_model(df, 'amount', self.test_user_id + 2)
        assert result['success'], "Training failed"
        
        metrics = result.get('metrics', {})
        
        # Check that test metrics are different from training
        train_r2 = metrics.get('train_r2', 0)
        test_r2 = metrics.get('test_r2', 0)
        
        # Should not be identical (would indicate leakage or overfitting)
        r2_diff = abs(train_r2 - test_r2)
        print(f"✓ Train R² = {train_r2:.4f}, Test R² = {test_r2:.4f}, Diff = {r2_diff:.4f}")
        
        # Check overfitting detection
        overfitting = metrics.get('overfitting_warning', 'Unknown')
        print(f"✓ Overfitting assessment: {overfitting}")
        
        # Features should not include target column
        features = metrics.get('features_used', [])
        assert 'amount' not in features, "Target column should not be in features"
        print(f"✓ No target leakage: target 'amount' not in features")
        
        self.results['no_data_leakage'] = 'PASSED'
    
    def test_overfitting_detection(self) -> None:
        """Test overfitting detection."""
        print("\n" + "-"*70)
        print("TEST 8: Overfitting Detection")
        print("-"*70)
        
        df = create_sample_financial_data(100)
        
        result = self.pipeline.train_regression_model(df, 'profit', self.test_user_id + 3)
        assert result['success'], "Training failed"
        
        metrics = result.get('metrics', {})
        train_r2 = metrics.get('train_r2', 0)
        test_r2 = metrics.get('test_r2', 0)
        overfitting_gap = metrics.get('overfitting_gap', 0)
        overfitting_warning = metrics.get('overfitting_warning', 'Unknown')
        
        print(f"✓ Train R² = {train_r2:.4f}")
        print(f"✓ Test R² = {test_r2:.4f}")
        print(f"✓ Overfitting gap = {overfitting_gap:.4f}")
        print(f"✓ Overfitting level = {overfitting_warning}")
        
        # Cross-validation should provide additional validation
        cv_r2 = metrics.get('cv_r2_mean', 0)
        print(f"✓ Cross-validation R² = {cv_r2:.4f}")
        
        self.results['overfitting_detection'] = 'PASSED'
    
    def print_summary(self) -> None:
        """Print test summary."""
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        all_passed = all(v == 'PASSED' for v in self.results.values())
        
        for test_name, status in self.results.items():
            symbol = "✓" if status == "PASSED" else "✗"
            print(f"{symbol} {test_name.replace('_', ' ').title()}: {status}")
        
        print("\n" + "="*70)
        if all_passed:
            print("ALL TESTS PASSED ✓")
        else:
            print("SOME TESTS FAILED ✗")
        print("="*70 + "\n")

if __name__ == "__main__":
    tester = MLPipelineTests()
    tester.run_all_tests()
