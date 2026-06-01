# cellml_file_slider_visualization
This repo develops tools to introduce sliders to cellml models for visualisation of the effects changes in parameter to allow rough and manual calibration to experimental data for initialisation of parameter calibration and  finding ranges for the parameters without any experimental data. This tool currently extracts the equations from cellml models and runs Runga-Kutta4 to evaluate the equation. Current tests are done on BG MWC Huang-Peskin SS.cellml which is a simple algebraic model, further tests on more sophisphistcated models are required.

#Firstly
Download the cellml_explorer.html, then clicking on the file should open a link where you are asked to upload a cellml file and a csv file

#Secondly,
On the top right-hand side of the tool, you will see t0, ti and n, meaning initial time, final time and time interval for simulation, adjust accordingly.

#Thirdly
You can add sliders for specific parameters by clicking the + buttom next to each parameter as well as add plots from a group of data encoded in the Dash2016___.CSV. Play around with the sliders and changes the ranges for parameter sliders to try and get good fits to experimental data
