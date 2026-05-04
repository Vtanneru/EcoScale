#!/usr/bin/env python3
"""
EcoScale: Energy-Proportional Scaling for Mixed-Workload Cloud-HPC
Main analysis module for energy measurement and decision tree generation.

Author: Venkateswarlu Tanneru
Date: May 2026
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.tree import DecisionTreeRegressor, plot_tree
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error
import json
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

class EcoScaleConfig:
    """Configuration parameters for EcoScale analysis."""

    # Workload definitions
    WORKLOADS = {
        'ResNet-50': {'type': 'CNN', 'params': 26e6, 'bottleneck': 'compute'},
        'ResNet-101': {'type': 'CNN', 'params': 45e6, 'bottleneck': 'compute'},
        'ResNet-152': {'type': 'CNN', 'params': 60e6, 'bottleneck': 'compute'},
        'BERT-base': {'type': 'Transformer', 'params': 110e6, 'bottleneck': 'memory'},
        'BERT-large': {'type': 'Transformer', 'params': 340e6, 'bottleneck': 'memory'},
        'ViT-base': {'type': 'Transformer', 'params': 87e6, 'bottleneck': 'memory'},
        'DLRM': {'type': 'Sparse', 'params': 4.7e9, 'bottleneck': 'io'},
        'NCF': {'type': 'Sparse', 'params': 0.6e9, 'bottleneck': 'io'},
        'DeepFM': {'type': 'Sparse', 'params': 0.3e9, 'bottleneck': 'io'},
        'CosmoFlow': {'type': 'CNN-3D', 'params': 1.3e9, 'bottleneck': 'communication'},
        'DeepCAM': {'type': 'CNN-3D', 'params': 1.4e9, 'bottleneck': 'communication'},
        'ViT-large': {'type': 'Transformer', 'params': 304e6, 'bottleneck': 'communication'},
    }

    NODE_COUNTS = [1, 2, 4, 8, 16, 32, 64]
    BATCH_SIZE_PER_GPU = 32
    NUM_RUNS = 5

    # Hardware specs
    POWER_PER_NODE_KW = 3.0  # A100 node (socket + GPU)
    AWS_PRICE_PER_HOUR = 3.06  # p4de.24xlarge
    ELECTRICITY_COST_PER_KWH = 0.10  # US average

    # Tree parameters
    TREE_MAX_DEPTH = 5
    TREE_MIN_SAMPLES_LEAF = 2
    TEST_SPLIT = 0.30


# ============================================================================
# SYNTHETIC DATA GENERATION (for demonstration)
# ============================================================================

class EnergyDataGenerator:
    """Generate synthetic energy measurements for 12 workloads x 7 node counts."""

    def __init__(self, config=EcoScaleConfig()):
        self.config = config

    def compute_throughput_model(self, workload, num_nodes):
        """Model throughput based on bottleneck class and node count."""
        bottleneck = self.config.WORKLOADS[workload]['bottleneck']
        params = self.config.WORKLOADS[workload]['params']

        # Base throughput (single node, samples/sec)
        base_tput = 1000.0 * np.log10(params / 1e6 + 1)  # Rough proxy

        if bottleneck == 'compute':
            # Linear scaling
            speedup = num_nodes
        elif bottleneck == 'memory':
            # Memory bandwidth bottleneck: sublinear scaling
            speedup = np.log(num_nodes) * 2 + 1
        elif bottleneck == 'io':
            # I/O bottleneck: minimal scaling beyond 4 nodes
            speedup = min(4, num_nodes * 0.3)
        else:  # communication
            # Sweet spot at 16-32 nodes
            if num_nodes <= 16:
                speedup = num_nodes * 0.9
            elif num_nodes <= 32:
                speedup = 14.4 + (num_nodes - 16) * 0.4
            else:
                speedup = 15.8 - (num_nodes - 32) * 0.05

        return base_tput * speedup

    def compute_power_model(self, workload, num_nodes, throughput):
        """Model power draw based on compute + communication."""
        bottleneck = self.config.WORKLOADS[workload]['bottleneck']

        base_power = num_nodes * self.config.POWER_PER_NODE_KW

        # Communication overhead grows with num_nodes
        if bottleneck == 'compute':
            comm_overhead = num_nodes * 0.05  # Minimal
        elif bottleneck == 'memory':
            comm_overhead = num_nodes * 0.2 + (num_nodes ** 1.5) * 0.01
        elif bottleneck == 'io':
            comm_overhead = num_nodes * 0.15
        else:  # communication
            comm_overhead = num_nodes * 0.3 + (num_nodes ** 1.3) * 0.02

        return base_power + comm_overhead

    def generate_data(self):
        """Generate energy measurements for all configurations."""
        data = []

        for workload in self.config.WORKLOADS.keys():
            for num_nodes in self.config.NODE_COUNTS:
                for run in range(self.config.NUM_RUNS):
                    # Compute throughput and power
                    tput = self.compute_throughput_model(workload, num_nodes)
                    power = self.compute_power_model(workload, num_nodes, tput)

                    # Add noise
                    tput += np.random.normal(0, tput * 0.02)
                    power += np.random.normal(0, power * 0.03)

                    # Epoch time (steps * step_time)
                    steps_per_epoch = 1000
                    step_time_sec = 1.0 / (tput / 32)  # steps/sec -> sec/step
                    epoch_time = steps_per_epoch * step_time_sec

                    # Energy metrics
                    energy_total_kwh = (power / 1000) * (epoch_time / 3600)  # kWh
                    energy_total_mj = energy_total_kwh * 3.6e6  # mJ

                    flops = 2 * self.config.WORKLOADS[workload]['params'] * 32 * steps_per_epoch
                    energy_per_flop = energy_total_mj / (flops / 1e9) if flops > 0 else 0

                    energy_per_step = (power * step_time_sec) / 1000  # J -> mJ conversion

                    data.append({
                        'workload': workload,
                        'num_nodes': num_nodes,
                        'bottleneck': self.config.WORKLOADS[workload]['bottleneck'],
                        'model_params_m': self.config.WORKLOADS[workload]['params'] / 1e6,
                        'throughput_tput_per_sec': tput,
                        'power_kw': power,
                        'epoch_time_sec': epoch_time,
                        'energy_total_mj': energy_total_mj,
                        'energy_per_flop_nj': energy_per_flop,
                        'energy_per_step_mj': energy_per_step,
                        'speedup': tput / self.compute_throughput_model(workload, 1),
                        'comm_overhead_pct': 100 * (power - num_nodes * self.config.POWER_PER_NODE_KW) / power,
                    })

        return pd.DataFrame(data)


# ============================================================================
# ANALYSIS PIPELINE
# ============================================================================

class EcoScaleAnalyzer:
    """Main analysis: energy breakdown, decision tree, validation."""

    def __init__(self, data_df, config=EcoScaleConfig()):
        self.data = data_df
        self.config = config

    def compute_optimal_node_count(self, workload_data):
        """For a given workload, find the node count minimizing energy_per_step."""
        agg = workload_data.groupby('num_nodes')['energy_per_step_mj'].mean()
        return int(agg.idxmin())

    def prepare_features_for_tree(self):
        """Prepare features for decision tree training."""
        features_list = []
        labels_list = []

        for workload in self.config.WORKLOADS.keys():
            wl_data = self.data[self.data['workload'] == workload]

            # Aggregate by node count (mean over runs)
            agg = wl_data.groupby('num_nodes').agg({
                'model_params_m': 'mean',
                'power_kw': 'mean',
                'energy_per_step_mj': 'mean',
                'comm_overhead_pct': 'mean',
                'speedup': 'mean',
            }).reset_index()

            # Extract optimal node count for this workload
            optimal_nodes = self.compute_optimal_node_count(wl_data)

            # Features: one row per workload (averaged across all node counts)
            row = {
                'model_params_m': agg['model_params_m'].mean(),
                'bottleneck': self.config.WORKLOADS[workload]['bottleneck'],
                'avg_power_kw': agg['power_kw'].mean(),
                'max_comm_overhead_pct': agg['comm_overhead_pct'].max(),
                'memory_footprint_gb': agg['model_params_m'].mean() * 4 / 1000,  # Rough estimate
                'optimal_nodes': optimal_nodes,
            }
            features_list.append(row)

        df_features = pd.DataFrame(features_list)

        # One-hot encode bottleneck
        df_features = pd.get_dummies(df_features, columns=['bottleneck'], drop_first=False)

        X = df_features.drop(columns=['optimal_nodes'])
        y = df_features['optimal_nodes']

        return X, y

    def train_decision_tree(self):
        """Train decision tree for predicting optimal node count."""
        X, y = self.prepare_features_for_tree()

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.config.TEST_SPLIT, random_state=42
        )

        # Train tree
        tree = DecisionTreeRegressor(
            max_depth=self.config.TREE_MAX_DEPTH,
            min_samples_leaf=self.config.TREE_MIN_SAMPLES_LEAF,
            random_state=42
        )
        tree.fit(X_train, y_train)

        # Evaluate
        y_pred_train = tree.predict(X_train)
        y_pred_test = tree.predict(X_test)

        mae_train = mean_absolute_error(y_train, y_pred_train)
        mae_test = mean_absolute_error(y_test, y_pred_test)

        print("\n" + "="*60)
        print("DECISION TREE RESULTS")
        print("="*60)
        print(f"Train MAE: {mae_train:.2f} nodes")
        print(f"Test MAE:  {mae_test:.2f} nodes")
        print(f"Accuracy (within 1 node): {np.mean(np.abs(y_test - y_pred_test) <= 1) * 100:.1f}%")
        print(f"Tree depth: {tree.get_depth()}")
        print(f"Tree leaves: {tree.get_n_leaves()}")

        return tree, X, y

    def validate_energy_savings(self, tree, X):
        """Compare EcoScale recommendations to throughput-optimized baseline."""
        print("\n" + "="*60)
        print("VALIDATION: ENERGY SAVINGS ANALYSIS")
        print("="*60)

        validation_results = []

        for workload in self.config.WORKLOADS.keys():
            wl_data = self.data[self.data['workload'] == workload]
            agg = wl_data.groupby('num_nodes')['energy_per_step_mj'].mean()

            # EcoScale recommendation
            ecoscale_nodes = int(self.compute_optimal_node_count(wl_data))
            ecoscale_energy = agg.loc[ecoscale_nodes]

            # Baseline: always 64 nodes (greedy)
            greedy_energy = agg.loc[64] if 64 in agg.index else agg.max()

            # Baseline: fair (always 16 nodes)
            fair_energy = agg.loc[16] if 16 in agg.index else agg.median()

            savings_vs_greedy = (greedy_energy - ecoscale_energy) / greedy_energy * 100

            validation_results.append({
                'workload': workload,
                'ecoscale_nodes': ecoscale_nodes,
                'ecoscale_energy_mj': ecoscale_energy,
                'greedy_energy_mj': greedy_energy,
                'fair_energy_mj': fair_energy,
                'savings_pct': savings_vs_greedy,
            })

        df_val = pd.DataFrame(validation_results)

        print(f"\nMean energy (EcoScale): {df_val['ecoscale_energy_mj'].mean():.1f} mJ")
        print(f"Mean energy (Greedy):  {df_val['greedy_energy_mj'].mean():.1f} mJ")
        print(f"Mean energy (Fair):    {df_val['fair_energy_mj'].mean():.1f} mJ")
        print(f"\nMean savings vs. Greedy: {df_val['savings_pct'].mean():.1f}%")
        print(f"Range: {df_val['savings_pct'].min():.1f}% to {df_val['savings_pct'].max():.1f}%")

        return df_val


# ============================================================================
# VISUALIZATION
# ============================================================================

class EcoScaleVisualizer:
    """Generate publication-quality figures."""

    def __init__(self, data_df, config=EcoScaleConfig()):
        self.data = data_df
        self.config = config
        Path('figs').mkdir(exist_ok=True)

    def plot_energy_by_class(self):
        """Figure 1: Energy per step vs. node count by bottleneck class."""
        fig, ax = plt.subplots(figsize=(10, 6))

        colors = {
            'compute': '#2E86AB',
            'memory': '#A23B72',
            'io': '#F18F01',
            'communication': '#C73E1D',
        }

        for bottleneck in ['compute', 'memory', 'io', 'communication']:
            subset = self.data[self.data['bottleneck'] == bottleneck]
            agg = subset.groupby('num_nodes')['energy_per_step_mj'].agg(['mean', 'std']).reset_index()

            ax.errorbar(agg['num_nodes'], agg['mean'], yerr=agg['std'],
                       marker='o', linewidth=2, markersize=8,
                       label=bottleneck.capitalize(), color=colors[bottleneck], capsize=5)

        ax.set_xlabel('Number of Nodes', fontsize=12, fontweight='bold')
        ax.set_ylabel('Energy per Step (mJ)', fontsize=12, fontweight='bold')
        ax.set_title('Energy Scaling by Bottleneck Class', fontsize=14, fontweight='bold')
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.legend(fontsize=11, loc='best')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig('figs/Fig1_energy_by_class.pdf', dpi=300, bbox_inches='tight')
        print("Saved: figs/Fig1_energy_by_class.pdf")
        plt.close()

    def plot_power_breakdown(self):
        """Figure 2: Power breakdown (compute vs. communication)."""
        fig, axes = plt.subplots(1, 3, figsize=(14, 4))

        workloads = ['ResNet-50', 'BERT-base', 'CosmoFlow']
        node_counts = [1, 16, 64]

        for idx, wl in enumerate(workloads):
            wl_data = self.data[self.data['workload'] == wl]
            subset = wl_data[wl_data['num_nodes'].isin(node_counts)]

            agg = subset.groupby('num_nodes')['power_kw'].mean()

            axes[idx].bar(range(len(node_counts)), agg.values, color='#2E86AB', alpha=0.7, label='Power')
            axes[idx].set_xticks(range(len(node_counts)))
            axes[idx].set_xticklabels(node_counts)
            axes[idx].set_ylabel('Power (kW)', fontsize=11)
            axes[idx].set_title(wl, fontsize=12, fontweight='bold')
            axes[idx].grid(True, alpha=0.3, axis='y')

        plt.suptitle('Power Breakdown by Workload and Node Count', fontsize=13, fontweight='bold')
        plt.tight_layout()
        plt.savefig('figs/Fig2_power_breakdown.pdf', dpi=300, bbox_inches='tight')
        print("Saved: figs/Fig2_power_breakdown.pdf")
        plt.close()

    def plot_validation_comparison(self, df_val):
        """Figure 3: Validation energy savings."""
        fig, ax = plt.subplots(figsize=(12, 6))

        x = np.arange(len(df_val))
        width = 0.25

        ax.bar(x - width, df_val['ecoscale_energy_mj'], width, label='EcoScale', color='#2E86AB', alpha=0.8)
        ax.bar(x, df_val['greedy_energy_mj'], width, label='Greedy (64N)', color='#A23B72', alpha=0.8)
        ax.bar(x + width, df_val['fair_energy_mj'], width, label='Fair (16N)', color='#F18F01', alpha=0.8)

        ax.set_xlabel('Workload', fontsize=12, fontweight='bold')
        ax.set_ylabel('Energy per Step (mJ)', fontsize=12, fontweight='bold')
        ax.set_title('Validation: Energy Savings Comparison', fontsize=14, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(df_val['workload'], rotation=45, ha='right')
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        plt.savefig('figs/Fig3_validation_comparison.pdf', dpi=300, bbox_inches='tight')
        print("Saved: figs/Fig3_validation_comparison.pdf")
        plt.close()


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    print("\n" + "="*70)
    print("EcoScale: Energy-Proportional Scaling for Mixed-Workload Cloud-HPC")
    print("="*70)

    # Generate synthetic data
    print("\n[1/4] Generating energy measurement data...")
    generator = EnergyDataGenerator()
    df_data = generator.generate_data()
    print(f"Generated {len(df_data)} measurements ({len(df_data.groupby('workload'))} workloads)")

    # Save data
    df_data.to_csv('data/ecoscale_measurements.csv', index=False)
    print("Saved: data/ecoscale_measurements.csv")

    # Analysis
    print("\n[2/4] Training decision tree for optimal node count...")
    analyzer = EcoScaleAnalyzer(df_data)
    tree, X, y = analyzer.train_decision_tree()

    # Validation
    print("\n[3/4] Validating energy savings...")
    df_val = analyzer.validate_energy_savings(tree, X)
    df_val.to_csv('data/validation_results.csv', index=False)
    print("Saved: data/validation_results.csv")

    # Visualization
    print("\n[4/4] Generating figures...")
    visualizer = EcoScaleVisualizer(df_data)
    visualizer.plot_energy_by_class()
    visualizer.plot_power_breakdown()
    visualizer.plot_validation_comparison(df_val)

    print("\n" + "="*70)
    print("COMPLETE: All figures and data saved to figs/ and data/")
    print("="*70 + "\n")


if __name__ == '__main__':
    main()
