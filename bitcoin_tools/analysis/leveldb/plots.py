from bitcoin_tools import CFG
from bitcoin_tools.analysis.plots import plot_distribution, get_cdf, plot_pie
from json import loads

from collections import Counter

def plot_from_file(x_attribute, y="tx", xlabel=False, log_axis=False, save_fig=False, legend=None,
                   legend_loc=1, font_size=20):
    """
    Generates plots from utxo/tx data extracted from utxo_dump.

    :param x_attribute: Attribute to plot (must be a key in the dictionary of the dumped data).
    :type x_attribute: str
    :param y: Either "tx" or "utxo"
    :type y: str
    :param xlabel: Label on the x axis
    :type xlabel: str
    :param log_axis: Determines which axis are plotted using (accepted values are False, "x", "y" or "xy").
    logarithmic scale
    :type log_axis: str
    :param save_fig: Figure's filename or False (to show the interactive plot)
    :type save_fig: str
    :param legend: List of strings with legend entries or None (if no legend is needed)
    :type legend: str list
    :param legend_loc: Indicates the location of the legend (if present)
    :type legend_loc: int
    :param font_size: Title, xlabel and ylabel font size
    :type font_size: int
    :return: None
    :rtype: None
    """

    if y == "tx":
        fin = open(CFG.data_path + 'parsed_txs.txt', 'r')
        ylabel = "Number of tx."
    elif y == "utxo":
        fin = open(CFG.data_path + 'parsed_utxos.txt', 'r')
        ylabel = "Number of UTXOs"
    else:
        raise ValueError('Unrecognized y value')

    samples = []
    for line in fin:
        data = loads(line[:-1])
        samples.append(data[x_attribute])

    fin.close()

    [xs, ys] = get_cdf(samples, normalize=True)
    title = ""
    if not xlabel:
        xlabel = x_attribute

    plot_distribution(xs, ys, title, xlabel, ylabel, log_axis, save_fig, legend, legend_loc, font_size)


def plot_from_file_dict(x_attribute, y="dust", fin_name=None, percentage=False, xlabel=False,
                        log_axis=False, save_fig=False, legend=None, legend_loc=1, font_size=20):

    """
    Generate plots from files in which the loaded data is a dictionary, such as dust.txt.

    :param x_attribute: Attribute to plot (must be a key in the dictionary of the dumped data).
    :type x_attribute: str
    :param y: Either "tx" or "utxo"
    :type y: str
    :param fin_name: Name of the file containing the data to be plotted.
    :type fin_name: str
    :param percentage: Whether the data is plot as percentage or not.
    :type percentage: bool
    :param xlabel: Label on the x axis
    :type xlabel: str
    :param log_axis: Determines which axis are plotted using (accepted values are False, "x", "y" or "xy").
    logarithmic scale
    :type log_axis: str
    :param save_fig: Figure's filename or False (to show the interactive plot)
    :type save_fig: str
    :param legend: List of strings with legend entries or None (if no legend is needed)
    :type legend: str list
    :param legend_loc: Indicates the location of the legend (if present)
    :type legend_loc: int
    :param font_size: Title, xlabel and ylabel font size
    :type font_size: int
    :return: None
    :rtype: None
    """

    fin = open(CFG.data_path + fin_name, 'r')
    data = loads(fin.read())

    # Decides the type of chart to be plot.
    if y == "dust":
        data_type = ["dust_utxos", "lm_utxos"]
        if not percentage:
            ylabel = "Number of utxos"
        else:
            ylabel = "Percentage of utxos"
            total = "total_utxos"
    elif y == "value":
        data_type = ["dust_value", "lm_value"]
        if not percentage:
            ylabel = "Value (Satoshi)"
        else:
            ylabel = "Percentage of total value"
            total = "total_value"
    elif y == "data_len":
        data_type = ["dust_data_len", "lm_data_len"]
        if not percentage:
            ylabel = "Utxos' size (bytes)"
        else:
            ylabel = "Percentage of total utxos' size"
            total = "total_data_len"
    else:
        raise ValueError('Unrecognized y value')

    xs = []
    ys = []
    # Sort the data
    for i in data_type:
        xs.append(sorted(data[i].keys(), key=int))
        ys.append(sorted(data[i].values(), key=int))

    title = ""
    if not xlabel:
        xlabel = x_attribute

    # If percentage is set, a chart with y axis as a percentage (dividing every single y value by the
    # corresponding total value) is created.
    if percentage:
        for i in range(len(ys)):
            if isinstance(ys[i], list):
                ys[i] = [j / float(data[total]) * 100 for j in ys[i]]
            elif isinstance(ys[i], int):
                ys[i] = ys[i] / float(data[total]) * 100

    # And finally plots the chart.
    plot_distribution(xs, ys, title, xlabel, ylabel, log_axis, save_fig, legend, legend_loc, font_size)


def plot_pie_chart_from_file(x_attribute, y="tx", title="", labels=[], groups=[], colors=[], save_fig=False, font_size=20):
    """
    Generates pie charts from UTXO/tx data extracted from utxo_dump.

    :param x_attribute: Attribute to plot (must be a key in the dictionary of the dumped data).
    :type x_attribute: str
    :param y: Either "tx" or "utxo"
    :type y: str
    :param labels: List of labels (one label for each piece of the pie)
    :type labels: str list
    :param groups: List of group keys (one list for each piece of the pie).
    :type groups: list of lists
    :param colors: List of colors (one color for each piece of the pie)
    :type colors: str lit
    :param save_fig: Figure's filename or False (to show the interactive plot)
    :type save_fig: str
    :param font_size: Title, xlabel and ylabel font size
    :type font_size: int
    :return: None
    :rtype: None
    """

    if y == "tx":
        fin = open(CFG.data_path + 'parsed_txs.txt', 'r')
        ylabel = "Number of tx."
    elif y == "utxo":
        fin = open(CFG.data_path + 'parsed_utxos.txt', 'r')
        ylabel = "Number of UTXOs"
    else:
        raise ValueError('Unrecognized y value')

    samples = []
    for line in fin:
        data = loads(line[:-1])
        samples.append(data[x_attribute])

    fin.close()

    # Count occurences
    ctr = Counter(samples)

    # Sum occurences that belong to the same pie group
    values = []
    for group in groups:
        group_value = 0
        for v in group:
            if v in ctr.keys():
                group_value += ctr[v]
        values.append(group_value)

    # Should we have an "others" section?
    if len(labels) == len(groups) + 1:
        # We assume the last group is "others"
        current_sum = sum(values)
        values.append(len(samples)-current_sum)

    plot_pie(values, labels, title, colors, save_fig=save_fig, font_size=font_size)


