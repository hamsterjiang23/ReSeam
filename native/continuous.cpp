// Fixed-topology adapter around the unmodified OptCuts continuous optimizer.
#include "Optimizer.hpp"
#include "SymDirichletEnergy.hpp"
#include "Timer.hpp"
#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>

OptCuts::MethodType methodType = OptCuts::MT_DISTMIN;
std::string outputFolderPath;
bool fractureMode = false;
std::ofstream logFile;
Timer timer(30), timer_step(30);
std::vector<std::pair<double, double>> energyChanges_bSplit, energyChanges_iSplit, energyChanges_merge;
std::vector<std::vector<int>> paths_bSplit, paths_iSplit, paths_merge;
std::vector<Eigen::MatrixXd> newVertPoses_bSplit, newVertPoses_iSplit, newVertPoses_merge;
double filterExp_in = 0.6;
int inSplitTotalAmt = 0;

template<class Matrix>
Matrix matrix_from(const boost::property_tree::ptree& node, int cols) {
    Matrix result(node.size(), cols);
    int row = 0;
    for (const auto& item : node) {
        if (item.second.size() != static_cast<size_t>(cols)) throw std::runtime_error("matrix shape");
        int col = 0;
        for (const auto& value : item.second)
            result(row, col++) = value.second.get_value<typename Matrix::Scalar>();
        ++row;
    }
    return result;
}

void write_result(const std::string& path, const Eigen::MatrixXd& uv, int iterations,
                  double energy, const std::string& status) {
    // The Python parent validates every checkpoint before accepting it.
    std::ofstream stream(path + ".tmp");
    stream << std::setprecision(17) << "{\"status\":\"" << status << "\",\"iterations\":"
           << iterations << ",\"energy_native\":" << energy << ",\"topology_updates\":0,\"uv\":[";
    for (int row = 0; row < uv.rows(); ++row) {
        if (row) stream << ',';
        stream << '[' << uv(row, 0) << ',' << uv(row, 1) << ']';
    }
    stream << "]}\n";
    stream.close();
    if (std::rename((path + ".tmp").c_str(), path.c_str())) throw std::runtime_error("checkpoint rename");
    std::ifstream input(path, std::ios::binary);
    std::ofstream checkpoint(path + ".checkpoint_" + std::to_string(iterations) + ".json", std::ios::binary);
    checkpoint << input.rdbuf();
}

int main(int argc, char** argv) {
    if (argc != 3) { std::cerr << "usage: reseam-continuous request.json result.json\n"; return 2; }
    try {
        boost::property_tree::ptree request;
        boost::property_tree::read_json(argv[1], request);
        const auto rest = matrix_from<Eigen::MatrixXd>(request.get_child("rest_vertices"), 3);
        const auto uv = matrix_from<Eigen::MatrixXd>(request.get_child("uv"), 2);
        const auto faces = matrix_from<Eigen::MatrixXi>(request.get_child("faces"), 3);
        if (rest.rows() != uv.rows() || !rest.allFinite() || !uv.allFinite() || faces.rows() == 0
            || faces.minCoeff() < 0 || faces.maxCoeff() >= uv.rows()) throw std::runtime_error("invalid input");
        std::set<int> fixed;
        for (const auto& value : request.get_child("fixed")) {
            int index = value.second.get_value<int>();
            if (index < 0 || index >= uv.rows()) throw std::runtime_error("fixed index");
            fixed.insert(index);
        }
        OptCuts::TriMesh mesh(rest, faces, uv, faces, false);
        mesh.resetFixedVert(fixed);
        OptCuts::SymDirichletEnergy energy;
        std::vector<OptCuts::Energy*> terms{&energy};
        std::vector<double> weights{1.0};
        double initial_energy;
        energy.computeEnergyVal(mesh, initial_energy);
        if (!std::isfinite(initial_energy)) throw std::runtime_error("nonfinite energy");
        write_result(argv[2], uv, 0, initial_energy, "INITIALIZED");
        if (fixed.size() == static_cast<size_t>(uv.rows())) return 0;
        OptCuts::Optimizer optimizer(mesh, terms, weights, 0, true, false);
        optimizer.precompute();
        optimizer.setRelGL2Tol(1e-10);
        const auto start = std::chrono::steady_clock::now();
        const int limit = request.get<int>("max_iterations", 160);
        const double seconds = request.get<double>("seconds", 30.0);
        for (int iteration = 0; iteration < limit; ++iteration) {
            if (std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() >= seconds) break;
            const int stopped = optimizer.solve(1);
            const auto& result = optimizer.getResult();
            if (result.F.rows() != faces.rows() || (result.F.array() != faces.array()).any())
                throw std::runtime_error("topology changed");
            for (int index : fixed)
                if ((result.V.row(index) - uv.row(index)).norm() > 1e-12)
                    throw std::runtime_error("fixed vertex moved");
            if (!result.V.allFinite()) throw std::runtime_error("nonfinite result");
            double value;
            energy.computeEnergyVal(result, value);
            write_result(argv[2], result.V, iteration + 1, value, stopped ? "CONVERGED" : "ITERATING");
            if (stopped) break;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
